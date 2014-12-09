#!/usr/bin/python
# Copyright (c) 2014, John Reumann, NoFutz Networks Inc., All Rights Reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#    * Neither the name of Nofutz Networks Inc. nor the
#      names of its contributors may be used to endorse or promote products
#      derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#
# Description:
# 
# The purpose of this script is to reconnect to a Wifi access point with a
# strong signal. This doesn't make sense in most setups. But if you have
# a slightly complicated setup with servers that should only be accessed
# from a subset of machines that are cleared to access your non-open
# wifi then you might want to configure this script.
#
# Here is an example setup that is fairly annoying on the admin's laptop
# which should have access to all machines in the network.
#
#
#                              10.1.0.0/24       -----------
#                             -------------------| Servers |
#                             |                  -----------
#                             |                  -----------         
#                             |    --------------| WifiOne |
#  Internet                   |   | 10.0.0.0/27  -----------
# -----------            ------------------
# | FIOS    |-1.2.3.0/30-| Gateway w/DHCP |
# -----------            ------------------ 
#                             | |  | 10.0.0.0/27 -----------
#                             | |  --------------| WifiTwo |
#                             | |                -----------
#                             | |
#                             | | 10.0.0.32/27   -----------
#                             |  -----------------| GuestOne|
#                             |                   -----------
#                             |   
#                             | 10.0.0.32/27     -----------
#                             -------------------| GuestTwo|
#                                                ___________
#                                     
#
# The interesting problem is that the access points all allow some ueber-
# boxes everywhere.  The guest networks are accessible to all boxes
# (including friends and children) within range.
# 
# Now the roaming isn't so easy for the ueberboxes. While, one can easily
# configure iptables on the gateway to work out the access policies, e.g.,
# no box on the port from our GuestOne|Two can initiate connections to internal
# machines. The problem happens when the admin may walks to the kitchen
# for a cup of coffee. Now, because of neighbor network interference and
# and proximity it may sometimes be best for the ueberbox to hop onto the
# Guest network to keep existing connections alive. New ssh connections to
# internal servers couldn't be initiated during this time but keeping
# alive those connections that already exist is useful.
#
# Prerequisite
# Network manager, iwconfig
#
# Function
# This script is installed on a client machine and typically run periodically.
# If the machine is connected to a preferred network with sufficient signal
# strength, there is no action taken. If there is a preferred network with
# better strength in reach the machine will reconnect to that network. If
# there is no preferred network with sufficient signal strength the laptop
# will reconnect to the strongest non-preferred network. To force switching
# between networks in the same preferredness category the singal strength
# must differ at least by --signal_quality_threshold
#
# Compatibility:
#   Ubuntu 14.04 (to adapt you might have to twiddle path names)
#
# Invocation:
#
#        python wifi-reconnctor.py --preferred='"prefer_me","me_too"' \
#                                  --not_preferred='"guest_one","guest_two"' \
#                                  --signal_quality_threshold=50 \
#                                  --signal_quality_delta_threshold=10 \
#                                  --unlock --lockfile /tmp/wifi-reconnect.lock
#
#   The above command says run as root wifi-reconnector.py. The preferred
#   networks are "prefer_me" and "me_too". The other networks that are
#   usable but not preferred are "guest_one" and "guest_two." Don't consider
#   connecting to networks which have less thatn 50% signal quality (test your
#   own acceptance levels) and don't switch unless the gain in signal quality
#   is at least 10%. If the reconnection script is locked, unlock it using
#   the file /tmp/wifi-reconnect.lock as our lockfile.
#
# Installation in crontab:
#   By far the most convenient/dangerous mode. The script unfortunately requires
#   root. The iwconfig tools need it. Best would be to run the script in a jail
#   for automation purposes.  A less safe way is sketched below.
#
#   Do this at your own risk
#     sudo chmod +s /sbin/iwconfig
#     sudo chmod +s /sbin/iwlist
#
#   Append something like the follwoing line to your crontab (e.g., call
#   crontab -e):
#   
#   1-59/5 0-23 * * * $HOME/bin/wifi-reconnctor.py --preferred='"sec1","sec2"'\
#     --not_preferred='"open1","open2"' --signal_quality_threshold=50\
#     --signal_quality_delta_threshold=10 --lockfile /tmp/reconnect.lock\
#     >> $HOME/tmp/wifi-reconnector.log 2>&1 

import csv
import datetime
import getopt
import os
import re
import subprocess
import sys
import time

# Global flags modified by the commandline
dry_run = False
interface = "wlan0"
lock = False
lockfile = None
non_preferred_wlans = []
preferred_wlans = []
signal_quality_delta_threshold = .15
signal_quality_lower_bound = .5
signal_quality_threshold = .5
unlock = False

# Distribution specific constants (Ubuntu 14.04)
IWLIST = '/sbin/iwlist'
SCANNING_COMMAND = 'scanning'

IWCONFIG = '/sbin/iwconfig'
NMCLI = '/usr/bin/nmcli'


def print_help():
    """Prints usage instructions
    """
    print "Usage: wifi-reconnector [--preferred \'<comma_separated_network"\
        "_names>\']\n"\
        "                        [--not_preferred \'<comma_separated_network"\
        "_names>\']\n\n" \
        "Each network name must be quoted in \"\" if it is longer than one "\
        "character.\n\n"
    print "Additional options:\n"
    print "  --signal_quality_deleta_threshold <1-100>: difference in signal"\
        " strength"
    print "    percent that causes a switch between networks in the"\
        " same preference class\n"
    print "  --signal_quality_threshold <1-100>: minimal signal"\
        " strength percent that"
    print "    causes us disassociate from a network\n" 
    print "  --interface <ifname>: which interface is wlan (default wlan0)\n"
    print "  --dry_run:  don't do anything just dump what would be done.\n"
    print "  --lockfile: path to lockfile. A path starting with \'/\' is"
    print "    absolute, without it is concatenated after $HOME\n"
    print "  --lock: lock the reconnector\n"
    print "  --unlock: unlock the reconnector\n"
    print "  --help or -h\n"


def match_iwconfig_v30_essid(output):
    """Returns the extracted ESSID from output, where output
    is presumed to be in the output format generated by iwconfig of
    version 30 or compatible
    """
    essid_pattern = re.compile('.+\s+ESSID:\"([^\"]+)\"')
    for line in output:
        if essid_pattern.match(line):
            return essid_pattern.match(line).group(1)


def is_locked(filename):
    """Returns true if the filename exists as a file in the
    underlying filesystem.
    """
    if None == filename:
        return False
    return os.path.isfile(filename)


def do_unlock(filename):
    """Unlocks by removing the filename if it exists as a file in the
    underlying filesystem
    """
    try:
        os.remove(filename)
    except OSError:
        pass


def do_lock(filename):
    """Locks by creating a file of filename in the underlying filesystem
    """
    try:
        file = open(filename, "w")
        file.write("locked\n")
        file.close()
        print_with_timestamp("Locked via file: %s" % filename)
        return True
    except IOError as err:
        bail_with_message("I/O error({0}): {1}".format(err.errno, err.strerror))


def bail_with_message(text):
    """Exits the process after printing ERROR: and text which is assumed
    to be a string.
    """
    sys.stderr.write("ERROR: %s\n" % text);
    sys.exit(-1)

def print_with_timestamp(text):
    """Prints the string text after a timestamp prefix
    """
    now = time.time()
    now_string = datetime.datetime.fromtimestamp(now).strftime(
        '%Y%m%d-%H:%M:%S')
    print "%s: %s" % (now_string, text)

def run_command_or_die(command):
    """Runs the command which is given as a list of commandline arguments.
    """
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        outs, errs = process.communicate()
        if (len(errs)):
            sys.stderr.write("Errors while executing %s:\n%s\n" % (command[0],
                                                                   errs))
        return outs, errs
    except OSError as err:
        bail_with_message("Failed to find or execute %s." % command[0])
    except subprocess.CalledProcessError:
        bail_with_messsage("Wifi scanning failed.");


def match_iwlist_v30_output(output):
    """Returns a dictionary of a all found wireless channels
    parsed from the output of iwlist. The returned dicitionary is keyed on
    the ESSID and each value for the keys is a set of parameters describing
    the wlan. This set is a dicitionary, too with the following fields.

       essid        : repeated the key for easier debugging
       cell         : wifi cell number as they apear in the listing output
       channel      : channel number
       quality      : devide the returned quality_numerator by denominator
                      into a bogus fractional representation. 
       signal_level : negative signal attenuation measured in dB
       address      : this is the base station MAC aka BSSID
       frequency    : normally a function of channel but depends on geography

    This function may need changes as iwlist evolves. Please change this to
    different versions and then add a function to autodetect the matcher
    that we should use.
    """
    cell_header_pattern = re.compile(
        '\s*Cell\s(\d+)\s.+Address: ([0-9ABCDEF:]+)')
    channel_pattern = re.compile('\s*Channel:(\d+)')
    freq_pattern = re.compile('\s*Frequency: ([\d\.]+)\s*GHz');
    quality_and_siqnal_pattern = re.compile(
        '\s*Quality=(\d+)/(\d+)\s+Signal level=-(\d+) dBm')
    essid_pattern = re.compile('\s*ESSID:\"([^\"]+)\"');

    parsed_set = { }
    cell = -1
    for line in output:
        if cell_header_pattern.match(line):
            if cell >= 0:
                parsed_set[essid] = {'essid': essid,
                                     'cell': cell,
                                     'channel': channel,
                                     'quality' : (float(quality_num) /
                                     float(quality_denom)),
                                     'signal_level' : signal_level,
                                     'address' : address,
                                     'frequency' : frequency, }
            # Reset our description
            cell = -1
            channel = -1
            quality_num = 0
            quality_denom = 1
            signal_level = 100
            essid = ''
            address = ''
            frequency = 0
            match = cell_header_pattern.match(line)
            cell = int(match.group(1))
            address = match.group(2)
        elif channel_pattern.match(line):
            match = channel_pattern.match(line)
            channel = int(match.group(1))
        elif freq_pattern.match(line):
            match = freq_pattern.match(line)
            frequency = float(match.group(1))
        elif quality_and_siqnal_pattern.match(line):
            match = quality_and_siqnal_pattern.match(line)
            quality_num = match.group(1)
            quality_denom = match.group(2)
            signal_level = match.group(3)
        elif essid_pattern.match(line):
            essid = essid_pattern.match(line).group(1)
    # Enter the last-scanned item into our dictionary before
    # we return
    if cell >= 0:
        parsed_set[essid] = {'essid': essid,
                             'cell': cell,
                             'channel': channel,
                             'quality' : (float(quality_num) /
                                          float(quality_denom)),
                             'signal_level' : signal_level,
                             'address' : address,
                             'frequency' : frequency,
        }

    return parsed_set


def parse_commandline_args():
    """Configures the global flag variables based on what the user
    specified on the commandline. Fails on bad commandlines.
    """
    global dry_run
    global interface
    global lock
    global lockfile
    global non_preferred_wlans
    global preferred_wlans
    global signal_quality_delta_threshold
    global signal_quality_threshold
    global unlock

    try:
        opts, args = getopt.getopt(sys.argv[1:],
                                   "h", ["help",
                                         "signal_quality_threshold=",
                                         "signal_quality_delta_threshold=",
                                         "preferred=",
                                         "not_preferred=",
                                         "lockfile=",
                                         "dry_run",
                                         "lock",
                                         "unlock"])
        for option, value in opts:
            if option in ('--help', '-h') :
                print_help()
                sys.exit(0)
            if option == "--lockfile":
                lockfile = value
            if option == "--lock":
                lock = True
            if option == "--unlock":
                unlock = True
            if option == "--signal_quality_delta_threshold":
                signal_quality_delta_threshold = int(value) / 100.
                if (signal_quality_delta_threshold < 0.01 or
                    signal_quality_delta_threshold > 1.):
                    bail_with_message(
                        "--signal_quality_delta_threshold should be in" \
                        "the 1-100 range")
            if option == "--signal_quality_threshold":
                signal_quality_threshold = int(value) / 100.
                if (signal_quality_threshold < 0.1 or
                    signal_quality_threshold > 1.):
                    bail_with_message(
                        "--signal_quality_threshold should be in"\
                        " 1-100 range")
            if option == "--preferred":
                # we use the csv reader to preserve commas in quotes, what
                # if the network name contains a comma
                for parsed in csv.reader([value], delimiter=',', quotechar='"'):
                    preferred_wlans = parsed
            if option == "--not_preferred":
                for parsed in csv.reader([value], delimiter=',', quotechar='"'):
                    non_preferred_wlans = parsed
            if option == "--interface":
                interface = value
            if option == "--dry_run":
                dry_run = True

    except getopt.GetoptError as err:
        print_with_timestamp(str(err))
        print_help()
        sys.exit(-1)


def scan_wifi(wlans):
    """Runs iwlist on all of the provided wlans. This allows picking up
    attributes of hidden wlans as well as those that are visible. For each
    detected WLAN we parse the iwlist output to get an indexed representation
    of our WLAN parameters.
    """
    global interface
    for wlan in wlans:
        command = [ IWLIST, interface, SCANNING_COMMAND, 'essid', wlan]
        outs, errs = run_command_or_die(command)
        return match_iwlist_v30_output(outs.split('\n'))

             
def get_active_wlan():
    """Runs iwconfig to determine the WLAN to which the interface is configured
    """
    command = [ IWCONFIG, interface]
    outs, errs = run_command_or_die(command)
    return match_iwconfig_v30_essid(outs.split('\n'))


def find_better_wifi(active_wifi, scanned_wifi_set):
    """Returns the WLAN to which the interface should be considered based
    on the parameters provided to this script on the commandline and the
    output that we gathered from iwlist (passed as scanned_wifi_set). If
    there is no WLAN that is better than the currently configured WLAN, then
    the function will return the name of the current WLAN. If the current
    WLAN looks terrible or there is logical better choice (e.g., a preferred
    WLAN at good quality) then that WLAN's name will be returned.
    """
    global preferred_wlans
    global non_preferred_wlans
    active_is_preferred = False
    if scanned_wifi_set == None or len(scanned_wifi_set) == 0:
        return active_wifi
    if active_wifi in preferred_wlans:
        active_is_preferred = True
    if (active_wifi in non_preferred_wlans) and active_is_preferred :
        bail_with_message("%s cannot be both preferred and non-preferred" %
                          active_wifi)
    allow_downgrade = False
    if (active_wifi == None) or (active_wifi == ""):
        bail_with_message("couln't find active wifi")
    if not active_wifi in scanned_wifi_set:
        bail_with_message("no iwlist info on active wifi %s", active_wifi)
    if scanned_wifi_set[active_wifi]['quality'] < signal_quality_lower_bound :
        allow_downgrade = True;

    sufficiently_better = active_wifi
    better_quality = scanned_wifi_set[active_wifi]['quality']
    sufficiently_better_is_preferred = active_is_preferred

    for wifi in preferred_wlans:
        if not wifi in scanned_wifi_set:
            continue
        upgrade_wifi_quality = scanned_wifi_set[wifi]['quality']
        if upgrade_wifi_quality < signal_quality_lower_bound:
            continue
        if not active_is_preferred:
            sufficiently_better = wifi
            better_quality = upgrade_wifi_quality
            continue
        # if the currently active wifi is still the best choice and
        # if it is not so bad that we'd accept a downgrade, we
        # impose a fudge difference before switching to a different
        # access point.
        if (sufficiently_better == active_wifi) :
            if not allow_downgrade:
                fudge = signal_quality_delta_threshold
        else:
            fudge = 0
        if (sufficiently_better < upgrade_wifi_quality - fudge) :
            sufficiently_better = wifi
            better_quality = upgrade_wifi_quality

    # if we leave the first loop with a network that has signal quality above
    # our cut-off and the wlan is preferred then we're done. Don't scane
    # for a non-preferred option
    if sufficiently_better_is_preferred and (better_quality >
                                             signal_quality_lower_bound) :
        return sufficiently_better

    for wifi in non_preferred_wlans:
        upgrade_wifi_quality = scanned_wifi_set[wifi]['quality']
        if upgrade_wifi_quality < signal_quality_lower_bound:
            continue
        if (sufficiently_better == active_wifi) :
            fudge = signal_quality_delta_threshold
        else:
            fudge = 0
        if (sufficiently_better < upgrade_wifi_quality - fudge) :
            sufficiently_better = wifi
            better_quality = upgrade_wifi_quality
    return sufficiently_better

    
def activate_wifi(wifi):
    """Contacts NetworkManager to change WLAN association to the WLAN given
    as wifi. This function does not check if we're already connected to wifi.
    """
    command = (NMCLI, 'c', 'up', 'id', wifi)
    outs, errs = run_command_or_die(command)


def get_path_full_or_relative_to_home(filename):
    """Returns an absolute path for filename. If filename is already absolute
    return that. If filename is relative, then we evaluate it relative to the
    calling users home directory, which we retrieve from the environment
    variables. If the HOME variable is not found and we were given a relative
    path, then None will be returned.
    """
    if filename == None:
        return None
    if filename[0] == '/':
        return filename
    home = os.getenv('HOME') 
    if home == None:
        return None
    return home + "/" + filename
    

def process_prescan_commands():
    """Runs any command that has nothing to do with scanning, i.e., 
    locking and unlocking this script.  Fails if the command couldn't
    be run.
    """
    global lock
    global lockfile
    global unlock

    if lock and unlock:
        bail_with_message("Cannot do both --lock and --unlock at "\
                          "the same time\n")

    if (lock or unlock) and (None == lockfile):
        bail_with_message("--lock and --unlock require a lockfile")

    fullpath = get_path_full_or_relative_to_home(lockfile)
    if lock:
        if not dry_run:
            do_lock(fullpath)
        else:
            print_with_timestamp("do_lock(%s)" % fullpath)
    if unlock:
        if not dry_run:
            do_unlock(fullpath)
        else:
            print_with_timestamp("do_unlock(%s)" % fullpath)
            
    if is_locked(fullpath):
        print_with_timestamp("Reconnect is locked via %s" % fullpath)
        return True
    return False


def main():
    global dry_run
    global non_preferred_wlans
    global preferred_wlans
    parse_commandline_args()

    if process_prescan_commands():
        return  # we already did work

    scanned_wifi = scan_wifi(preferred_wlans)
    active_wifi = get_active_wlan()
    better_wifi = find_better_wifi(active_wifi, scanned_wifi)
    if not (better_wifi == active_wifi):
        if not dry_run:
            print_with_timestamp(
                "Switching active wifi %s -> %s " % (active_wifi, better_wifi))
            activate_wifi(better_wifi)
        else:
            print_with_timestamp("activate_wifi(%s)" % better_wifi)
    else:
        print_with_timestamp("Staying on %s" % active_wifi)
    

if __name__ == "__main__":
    main()
