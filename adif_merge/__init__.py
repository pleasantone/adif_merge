#!/usr/bin/python3

"""
adif_merge.py
=============

Ham Radio ADIF Logbook format merge/resolution program written in Python

See README.rst for more information.


Copyright & License
-------------------
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
"""
import argparse
import csv
import json
import logging
import math
import re
import os
from datetime import datetime, timedelta

import adif_io

__VERSION__ = "1.0.3"

# merge any calls in the same band and same mode within 115 seconds
MERGE_WINDOW = 115


# WSJT-X generated fields, minimal output fields as well
FIELD_ORDER = [
    'CALL',
    'GRIDSQUARE',
    'MODE', 'SUBMODE',
    'RST_SENT', 'RST_RCVD',
    'QSO_DATE', 'TIME_ON',
    'QSO_DATE_OFF', 'TIME_OFF',
    'BAND',
    'FREQ',
    'STATION_CALLSIGN',
    'MY_GRIDSQUARE',
    'TX_PWR',
    'COMMENT',
    'NAME',
]

# ADIF 3.1.0 specifies field properties
FIELD_INTEGERS = [
    'K_INDEX', 'NR_BURSTS', 'NR_PINGS', 'SFI', 'SRX', 'STX']
FIELD_INTEGERS_POS = [
    'CQZ', 'FISTS', 'FISTS_CC', 'IOTA_ISLAND_ID', 'ITUZ',
    'MY_CQ_ZONE', 'MY_FISTS', 'MY_IOTA_ISLAND_ID', 'MY_ITU_ZONE',
    'TEN_TEN', 'UKMSG']
FIELD_NUMBERS = [
    'AGE', 'A_INDEX', 'ANT_AZ', 'ANT_EL', 'DISTANCE', 'FREQ', 'FREQ_RX',
    'MAX_BURSTS', 'RX_PWR', 'TX_PWR']
FIELD_ZONES = ['MY_CQ_ZONE', 'CQZ', 'MY_ITU_ZONE', 'ITUZ']


def fixup_qso(qso, path=None):
    """
    Pre-process an individual QSO record upon load and fix common mistakes.
    """
    for field in qso.keys():
        if isinstance(qso[field], str):
            qso[field] = qso[field].strip()
    # fixup misreporting of FT4 mode
    if qso.get('MODE') == "FT4":
        qso['MODE'] = "MFSK"
        qso['SUBMODE'] = "FT4"
    # TX_PWR should only be digits
    for field in ['TX_PWR', 'RX_PWR']:
        if field in qso:
            if qso[field] == "NaN":
                del qso[field]
            else:
                match = re.search(r'([\d\.]+)[Ww]', qso[field])
                if match:
                    qso[field] = match.group(1)
    # if the field is a "PositiveInteger" or "Integer" field, make it an int
    for field in FIELD_INTEGERS + FIELD_INTEGERS_POS:
        if field in qso:
            qso[field] = int(qso[field])
    # if the field is a "Number" make it a float, unless it's whole in which case int
    for field in FIELD_NUMBERS:
        if field in qso:
            qso[field] = float(qso[field])
            if field in ['FREQ', 'FREQ_TX']:
                # round to 3 digits
                qso[field] = round(qso[field], 3)
            else:
                # leave as an int if possible, otherwise float
                (part, whole) = math.modf(float(qso[field]))
                if not part:
                    qso[field] = int(whole)
    # band should always be uppercase
    for field in ['BAND', 'BAND_RX']:
        if field in qso:
            qso[field] = qso[field].upper()
    # some log sources replace / with _, restore /
    for field in ['CALL', 'MYCALL']:
        if field in qso:
            qso[field] = qso[field].replace("_", "/").upper()
    # properly "caseify" gridsquares... it's unnecessary but pleasant
    for field in ['GRIDSQUARE', 'MY_GRIDSQUARE']:
        if field in qso:
            qso[field] = "{}{}".format(
                qso[field][0:4].upper(), qso[field][4:].lower())
    # remove bad LAT/LON entries
    for field in ['LAT', 'LON']:
        if field in qso and qso[field][1:] == "000 00.000":
            del qso[field]
    if path:
        qso['_SOURCE_FILE'] = path
    return qso


# If dupe comes from one of these sources, prefer dupe records over
# anything else we've already merged.
SOURCE_OVERRIDES = {
    'LOTW': r'APP_LOTW_|LOTW_|AARL_SECT|DXCC$|COUNTRY$',
    'QRZ':  r'APP_QRZCOM_|QRZCOM_',
    'EQSL': r'APP_EQSL_|EQSL',
    'CLUBLOG': r'APP_CLUBLOG_|CLUBLOG_',
    'HRDLOG': r'APP_HRDLOG_|HRDLOG_',
}


def merge_dupe_fields(field, first, dupe):
    """
    Merge duplicate fields between two QSO records.
    """
    if field[0] == "_":                 # don't touch internal metadata
        return
    if field not in dupe:
        return
    if field not in first:
        first[field] = dupe[field]
        del dupe[field]
        return
    if first[field] == dupe[field]:
        del dupe[field]
        return
    if field in ['CNTY']:
        fnsfc = first[field].replace(" ", "").casefold()
        dnsfc = dupe[field].replace(" ", "").casefold()
        if fnslc == dnslc:
            # if dupe had spaces, use the one with spaces
            if len(first[field]) < len(dupe[field]):
                first[field] = dupe[field]
            del dupe[field]
        elif fnslc in dnslc:
            first[field] = dupe[field]
            del dupe[field]
        elif dnslc in fnslc:
            del dupe[field]
    if field in ['NAME', 'COMMENT']:
        # prefer mixed case to uppercase only entries
        if first[field].casefold() == dupe[field].casefold():
            if first[field].isupper():
                first[field] = dupe[field]
            del dupe[field]
    if field in ['TIME_ON', 'TIME_OFF', 'GRIDSQUARE']:
        # handle the present but empty case
        if not first[field]:
            first[field] = dupe[field]
            del dupe[field]
        elif not dupe[field]:
            del dupe[field]
        elif first[field][0:4] == dupe[field][0:4]:
            # chose the field with higher precision
            if len(dupe[field]) > len(first[field]):
                first[field] = dupe[field]
                del dupe[field]
            elif len(dupe[field]) <= len(first[field]):
                del dupe[field]
    if field in ['QSL_RCVD']:
        if dupe[field] == 'Y':
            first[field] = 'Y'
        del dupe[field]
    if field in ['RST_SENT', 'RST_RCVD']:
        # prefer +/- reports over 3-digit reports which were probably
        # generated by default by a non-digital logging program
        if (re.match(r'\d\d\d', first[field]) and
                re.match(r'[+-]\d\d', dupe[field])):
            first[field] = dupe[field]
            del dupe[field]
    for source, match in SOURCE_OVERRIDES.items():
        if source in dupe['_SOURCE_FILE'].upper() and re.match(match, field):
            first[field] = dupe[field]
            del dupe[field]


def merge_two_qsos(first, dupe):
    """
    Merge the fields that we can in both QSOs, leave the dupe
    as a "runt" entry in qso['_UNMERGED'] on the first QSO if necessary.
    """
    fields = set(first.keys()).union(set(dupe.keys()))
    for field in fields:
        merge_dupe_fields(field, first, dupe)
    if len(dupe) > 1:
        if '_UNMERGED' not in first:
            first['_UNMERGED'] = {}
        first['_UNMERGED'][dupe['_SOURCE_FILE']] = dupe
    if '_SOURCE_FILE' in first:
        del first['_SOURCE_FILE']
    if '_SOURCE_FILE' in dupe:
        del dupe['_SOURCE_FILE']
    return first


def merge_qsos(qsos, window):
    """
    First bucketize all QSOs by unique fields, then chunk them off by time
    """
    buckets = {}
    for qso in sorted(qsos, key=adif_io.time_on):
        key = "{}_{}_{}".format(qso['CALL'], qso['BAND'], qso['MODE'])
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(qso)

    # this depends upon sorted above
    for entries in buckets.values():
        first = entries[0]
        cutoff = adif_io.time_on(first) + timedelta(seconds=window)
        for qso in entries[1:]:
            if adif_io.time_on(qso) < cutoff:
                merge_two_qsos(first, qso)
            else:
                first = qso
                cutoff = adif_io.time_on(first) + timedelta(seconds=window)

    # remove any residual unmerged crap from the top list
    for entry, values in buckets.items():
        buckets[entry] = [qso for qso in values if 'CALL' in qso]

    merged_qsos = []
    for entries in buckets.values():
        merged_qsos.extend(entries)

    return sorted(merged_qsos, key=adif_io.time_on)


def dump_problems(qsos, path):
    """
    Report any unmerged fields, break the problem report down both
    by field, and by qso and output the report as a .json file
    """
    problems = [qso for qso in qsos if '_UNMERGED' in qso]
    dupe_fields = {}
    for qso in problems:
        for source, dupe in qso['_UNMERGED'].items():
            qso_id = "{}_{}_{}_{}".format(
                qso['CALL'], qso['QSO_DATE'], qso['TIME_ON'], qso['BAND'])
            for field in dupe.keys():
                if field not in dupe_fields:
                    dupe_fields[field] = {
                        'count': 0,
                        'qsos': {}
                    }
                if qso_id not in dupe_fields[field]['qsos'].keys():
                    dupe_fields[field]['count'] += 1
                    dupe_fields[field]['qsos'][qso_id] = {
                        '#SELECTED#': qso[field]
                    }
                dupe_fields[field]['qsos'][qso_id][source] = dupe[field]
    if problems:
        report = {
            'problems_by_field': dupe_fields,
            'problems_by_qso': problems,
        }
        with open(path, "w") as wfd:
            json.dump(report, wfd, indent=4, sort_keys=True)


def adif_write_field(stream, field, entry, comment=""):
    """
    Write a single field out for a QSO in <field:length>[data] format.
    Separate them with spaces.
    """
    if field in FIELD_ZONES:
        entry = "{:02d}".format(int(entry))
    else:
        entry = str(entry)
    if comment:
        comment = " //" + comment
    print("<{}:{}>{}{}".format(field.lower(), len(entry), entry, comment),
          file=stream, end=" ")


def adif_write(stream, qsos, minimal=False):
    """
    Write an array of QSOs to an ADIF file stream.
    """
    adif_write_field(stream, "adif_ver", "3.1.0")
    adif_write_field(stream, "created_timestamp",
                     "{:%Y%m%d %H%M%S}".format(datetime.utcnow()))
    adif_write_field(stream, "programid", "logmerge_pst")
    adif_write_field(stream, "programversion", __VERSION__)
    print("<eoh>", file=stream)
    for qso in qsos:
        for field in FIELD_ORDER:
            if field in qso:
                adif_write_field(stream, field, qso[field])
        if not minimal:
            for field in sorted(qso):
                if field[0] == "_":
                    continue
                if field not in FIELD_ORDER:
                    adif_write_field(stream, field, qso[field])
        print("<eor>", file=stream)


def date_format_wsjt(native) -> str:
    """
    Format a date field in WSJT-X native .log csv format
    """
    if native:
        return "{}-{}-{}".format(native[0:4], native[4:6], native[6:8])
    return ""


def time_format_wsjt(native) -> str:
    """
    Format a time field in WSJT-X native .log csv format
    """
    if native:
        if len(native) == 6:
            return "{}:{}:{}".format(native[0:2], native[2:4], native[4:6])
        if len(native) == 4:
            return "{}:{}:00".format(native[0:2], native[2:4])
        logging.error("%s: bad time field", native)
        return "ERROR"
    return ""


def csv_write(csvfile, qsos) -> None:
    """
    Write the final merged list of QSOs to a WSJT-X compatible
    CSV file.
    """
    writer = csv.writer(
        csvfile, delimiter=',', quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator=os.linesep)
    for qso in qsos:
        writer.writerow([
            date_format_wsjt(qso['QSO_DATE']),
            time_format_wsjt(qso['TIME_ON']),
            date_format_wsjt(qso.get('QSO_DATE_OFF')),
            time_format_wsjt(qso.get('TIME_OFF')),
            qso['CALL'],
            qso.get('GRIDSQUARE', ""),
            qso.get('FREQ', ""),
            qso.get('SUBMODE', qso.get('MODE', "")),
            qso.get('RST_SENT', ""),
            qso.get('RST_RCVD', ""),
            qso.get('TX_PWR', ""),
            qso.get('COMMENT', ""),
            qso.get('NAME', "")
        ])


def read_adif_file(path) -> list:
    """
    Attempt to read and process an ADIF file and return all of the
    QSO information.

    This is complicated by the fact that LoTW files may not be unicode
    encoded, and might be ISO-8859-15. First attempt to read the file
    using unicode, should that fail, revert to character detection.
    """
    try:
        with open(path) as adif_file:
            adif_string = adif_file.read()
    except UnicodeDecodeError:
        with open(path, encoding="latin-1") as adif_file:
            adif_string = adif_file.read()
    return adif_io.read_from_string(adif_string)


def main():
    """
    Load ADIF files, clean each qso individually produce output
    """
    parser = argparse.ArgumentParser(description="Merge ADIF files")
    parser.add_argument('--problems', '-p', type=str,
                        help="Intermediate problem output .json")
    parser.add_argument('--output', '-o', type=str,
                        default="qso_merged.adif")
    parser.add_argument('--minimal', '-m', action='store_true',
                        help="Only output important fields")
    parser.add_argument('--merge-window', type=int, default=MERGE_WINDOW,
                        help="Time window for merging discrepent log entries")
    parser.add_argument('--csv', '-c', type=str,
                        help="WSJT-X compatible .log file")
    parser.add_argument('--version', '-v', action='version',
                        version="%(prog)s {version}".format(version=__VERSION__))
    parser.add_argument('input', type=str, nargs="+",
                        help="Input file list")
    args = parser.parse_args()

    qsos = []
    for path in args.input:
        raw, _adif_header = read_adif_file(path)
        processed = [fixup_qso(qso, os.path.basename(path)) for qso in raw]
        qsos += processed

    qsos = merge_qsos(qsos, args.merge_window)

    if args.problems:
        dump_problems(qsos, args.problems)

    if args.output:
        with open(args.output, "w") as adiffile:
            adif_write(adiffile, qsos, args.minimal)

    if args.csv:
        with open(args.csv, "w") as csvfile:
            csv_write(csvfile, qsos)


if __name__ == "__main__":
    main()
