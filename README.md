# adif_merge.py

Ham Radio ADIF Logbook format merge/resolution program written in Python

## Summary

This tool is designed to merge and resolve multiple ADIF files, including
partial information from different reported sources (e.g. previous
uploads to LoTW, QRZ, clublog, et al. Each of these sources tend to
"augment" log entries with their own additional information.

The motivation for this is that GridTracker <https://tagloomis.com/> and
several logging programs are able to not only send log entries to remote
services, but also automatically download ADIF files back from them.
I found myself with a bunch of ADIF files, but none of them really gave
me the whole picture of my QSOs. I also have both a home and portable
station and sometimes I'd forget to move my logs between the two. This
allows me to merge those logs at a later date (or reconstruct them from
external servers).

The code will look at multiple log entries that occur with the same band,
call, and mode within 90 seconds of each other and attempt to merge
them, since some reporting tools or duplicate logging out of WSJT-X
occasionally occurs (e.g. a manual log entry to correct a gridsqare,
or different rounding of times on and off (to the nearest minute).

It tries to automate the decision making process for conflicts between
log entries, and will tend to treat .adif files with "lotw" in their
name as more authoritative for some fields.

For a complete look at the decision making process, read the code.  It's
commented, including caveats and is designed to be easily modifiable.

Use the `-p <filename>.json` option to generate problem QSO output in
JSON format to see where there were conflicts and how we resolved them.

## Installation

Developed under python 3 >= 3.6

```
    pip3 install adif_merge
```

## Sample usage

Here's what I do to merge my WSJT and GridTracker managed logs::

    adif_merge -o mergedlog.adif -c merged_wsjtx.log -p problems.json \
            ~/.local/share/WSJT-X/wsjtx_log.adi ~/Documents/GridTracker/*.adif

Please use the `--problems` option to look at merge issues that the
program wasn't confident about resolving.  For example QRZ and LoTW
often differ about user-entered information like ITU and CQ zones.

The problems option will a .json file that is approximately human readable
list of unresolved issues you may wish to fix--first organized by field,
and again organized by QSO.


## Feedback & Disclaimer

This code is learning and evolving. Please save copies of all of your
log files before replacing them with this augmented file.

If you disagree with choices I've made in preference when attempting
to merge, such as frequency harmonization or deferring to LoTW when
there is a conflict for some fields, please let me know.


## Copyright & License

Copyright (c) 2020 by Paul Traina, All rights reserved.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
