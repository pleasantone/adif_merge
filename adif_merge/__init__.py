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
import string
from datetime import datetime, timedelta

import adif_io

__PROGRAM__ = "adif_merge_pst"
__VERSION__ = "1.1.3"
__STANDARD__= "3.1.0"

# merge any calls in the same band and same mode within 115 seconds
MERGE_WINDOW = 115

# merge any distances that are less than 5 (mi/km) or < 15% different
MERGE_DISTANCE_ABS = 5
MERGE_DISTANCE_PCT = 0.15

# ADIF files are supposed to be strict ascii, but conventionally they are really ISO-8859-1
ADIF_ENCODING = "latin-1"

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

# You can't have a QSO without these minimum things (ADIF 3.1.0 spec)
FIELD_MANDATORY = [
    'QSO_DATE',
    'TIME_ON',
    'CALL',
    'BAND',
    'MODE'
]

# ADIF 3.1.0 specifies field properties
FIELD_INTEGERS = [
    'K_INDEX', 'NR_BURSTS', 'NR_PINGS', 'SFI', 'SRX', 'STX',
    # this is an enumeration but treat it as an integer
    'DXCC']
FIELD_INTEGERS_POS = [
    'CQZ', 'FISTS', 'FISTS_CC', 'ITUZ',
    'MY_CQ_ZONE', 'MY_FISTS', 'MY_ITU_ZONE',
    'TEN_TEN', 'UKMSG']
FIELD_NUMBERS = [
    'AGE', 'A_INDEX', 'ANT_AZ', 'ANT_EL', 'DISTANCE', 'FREQ', 'FREQ_RX',
    'MAX_BURSTS', 'RX_PWR', 'TX_PWR']
FIELD_ZONES = ['MY_CQ_ZONE', 'CQZ', 'MY_ITU_ZONE', 'ITUZ']

# these are not complete, just the common screwups
FIELD_MODES = {
    'DOMINO': ['DOMINOEX', 'DOMINOF'],
    'JT4': ['JT4A', 'JT4B', 'JT4C', 'JT4D', 'JT4E', 'JT4F', 'JT4G'],
    'JT65': ['JT65A', 'JT65B', 'JT65B2', 'JT65C', 'JT65C2'],
    'JT9': ['JT9-1', 'JT9-2', 'JT9-5', 'JT9-10', 'JT9-30',
            'JT9A', 'JT9B', 'JT9C', 'JT9D', 'JT9E', 'JT9E FAST', 'JT9F', 'JT9F FAST',
            'JT9G', 'JT9G FAST', 'JT9H', 'JT9H FAST'],
    'MFSK': ['FSQCALL', 'FT4', 'JS8', 'MFSK4', 'MFSK8', 'MFSK11', 'MFSK16',
             'MFSK22', 'MFSK31', 'MFSK32', 'MFSK64', 'MFSK128'],
    'OLIVIA': ['OLIVIA 4/125', 'OLIVIA 4/250', 'OLIVIA 8/250', 'OLIVIA 8/500',
               'OLIVIA 16/500', 'OLIVIA 16/1000', 'OLIVIA 32/1000'],
    'PSK': ['FSK31', 'PSK10', 'PSK31', 'PSK63', 'PSK63F', 'PSK125', 'PSK250', 'PSK500',
            'PSK1000', 'PSKAM10', 'PSKAM31', 'PSKAM50', 'PSKFEC31', 'QPSK31', 'QPSK63',
            'QPSK125', 'QPSK250', 'QPSK500', 'SIM31'],
    'QRA64': ['QRA64A, QRA64B, QRA64C, QRA64D, QRA64E'],
    'RTTY': ['ASCI'],
    'SSB': ['USB', 'LSB'],
}
FIELD_MODES_REVERSE = {
    submode: mode for mode, submodes in FIELD_MODES.items() for submode in submodes
}


class QSOError(ValueError):
    """Malformed ADIF QSO Entry"""


_WSTRANS = str.maketrans('', '', string.whitespace)

def comparable_string(val):
    """Remove all whitespace including crlf, tab, everything and casefold"""
    return val.casefold().translate(_WSTRANS)


def fixup_qso_mode(qso):
    """
    Some log programs don't follow the ADIF spec on modes and submodes, fix them
    """
    mode = qso['MODE']
    real_mode = FIELD_MODES_REVERSE.get(mode)
    if real_mode:
        qso['MODE'] = real_mode
        qso['SUBMODE'] = mode
    return qso


def fixup_qso(qso, path=""):
    """
    Pre-process an individual QSO record upon load and fix common mistakes.
    """
    if path:
        qso['_SOURCE_FILE'] = path
    # if we're missing a madatory field, mark the QSO and do not process any of it
    missing_mandatory = {field for field  in FIELD_MANDATORY if field not in qso}
    if missing_mandatory:
        qso['_MISSING_FIELDS'] = list(missing_mandatory)
        raise QSOError("{}: {} missing {}".format(
            path,
            "/".join([qso.get(field, field.lower()) for field in FIELD_MANDATORY]),
            ", ".join(missing_mandatory)), qso)
    for field in qso.keys():
        if isinstance(qso[field], str):
            qso[field] = qso[field].strip()
    qso = {key: value for key, value in qso.items() if value}
    qso = fixup_qso_mode(qso)
    # TX_PWR should only be digits
    for field in ['TX_PWR', 'RX_PWR']:
        if field in qso:
            if qso[field] == "NaN":
                del qso[field]
            else:
                match = re.search(r'([\d\.]+)[Ww]', qso[field])
                if match:
                    qso[field] = match.group(1)
                qso[field] = float(qso[field])
                if qso[field] > 10000:
                    qso[field] //= 10000
    # if the field is a "PositiveInteger" or "Integer" field, make it an int
    # some broken logbooks (e.g. HRD) generate Numbers where there should be
    # Integers--accept them but turn them into ints.
    for field in FIELD_INTEGERS + FIELD_INTEGERS_POS:
        if field in qso:
            qso[field] = int(float(qso[field]))
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
    # remove bogus zero fields (DXCC zero is valid)
    for field in ['A_INDEX', 'K_INDEX', 'SFI', 'DISTANCE', 'TX_PWR', 'RX_PWR'] + FIELD_ZONES:
        if field in qso and not qso[field]:
            del qso[field]
    # look for a redundant comment field that matches our RST_SENT and RST_RCVD
    for field in ['COMMENT', 'NOTES']:
        if field in qso and 'RST_SENT' in qso and 'RST_RCVD' in qso:
            match = re.search(r'Sent:?\s*([+-]?\d+)\s+Rcvd:?\s*([+-]?\d+)',
                              qso[field], re.IGNORECASE)
            if match and match.group(1) == qso['RST_SENT'] and match.group(2) == qso['RST_RCVD']:
                del qso[field]
    if qso.get('IOTA', "").replace("-", "").lower().strip() == "none":
        del qso['IOTA']
    return qso


# If dupe comes from one of these sources, prefer dupe records over
# anything else we've already merged.
SOURCE_OVERRIDES = {
    'LOTW': r'APP_LOTW_|LOTW_|AARL_SECT|DXCC$|COUNTRY$',
    'QRZ':  r'APP_QRZCOM_|QRZCOM_|APP_QRZLOG_',
    'EQSL': r'APP_EQSL_|EQSL',
    'CLUBLOG': r'APP_CLUBLOG_|CLUBLOG_',
    'HRD': r'APP_HRDLOG_|HRDLOG_|APP_HAMRADIODELUXE?_|HRDCOUNTRYNO$',
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
    if field in ['NAME', 'MY_NAME', 'ADDRESS', 'MY_ADDRESS', 'STREET', 'MY_STREET',
                 'CITY', 'MY_CITY', 'CNTY', 'MY_CNTY', 'STATE', 'MY_STATE',
                 'COUNTRY', 'MY_COUNTRY',
                 'MY_RIG', 'COMMENT', 'EMAIL', 'QSLMSG', 'WEB', 'PFX', 'QSL_VIA', 'QTH']:
        fnslc = comparable_string(first[field])
        dnslc = comparable_string(dupe[field])
        # if dupe is identical but had whitespace, use the one with whitespace
        # else use the longer one if one is a substring of the other
        if fnslc == dnslc:
            if len(first[field]) < len(dupe[field]) or first[field].isupper():
                first[field] = dupe[field]
            del dupe[field]
        elif fnslc in dnslc:
            first[field] = dupe[field]
            del dupe[field]
        elif dnslc in fnslc:
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
    if field in ['DISTANCE']:
        # if the distance difference is less that 5 miles or 15%, choose the longer
        difference = abs(first[field] - dupe[field])
        if difference < MERGE_DISTANCE_ABS or difference / first[field] < MERGE_DISTANCE_PCT:
            first[field] = max(first[field], dupe[field])
            del dupe[field]
    if field in ['FREQ', 'FREQ_RX']:
        if abs(first[field] - dupe[field]) < 0.01:
            first[field] = max(first[field], dupe[field])
            del dupe[field]
    if field in ['QSL_RCVD', 'QSL_SENT', 'EQSL_QSL_SENT', 'EQSL_QSL_RCVD',
                 'LOTW_QSL_SENT', 'LOTW_QSL_RCVD', 'QSO_RANDOM']:
        if first[field] in ['N', 'R'] and dupe[field] in ['Y', 'V']:
            first[field] = dupe[field]
        del dupe[field]
    if field in ['RST_SENT', 'RST_RCVD']:
        # prefer +/- reports over 3-digit reports which were probably
        # generated by default by a non-digital logging program
        if (re.match(r'\d\d\d', first[field]) and
                re.match(r'[+-]\d\d', dupe[field])):
            first[field] = dupe[field]
            del dupe[field]
    if field in ['DXCC', 'A_INDEX', 'K_INDEX', 'SFI'] + FIELD_ZONES:
        if dupe[field] and not first[field]:
            first[field] = dupe[field]
            del dupe[field]
    if field in dupe:
        for source, match in SOURCE_OVERRIDES.items():
            if re.match(match, field):
                if source in dupe['_SOURCE_FILE'].upper():
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
        key = "{}_{}_{}_{}".format(qso['CALL'], qso['BAND'], qso.get('MODE'), qso.get('SUBMODE'))
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


def dump_problems(qsos, malformed, path):
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
    if problems or malformed:
        report = {
            'problems_by_field': dupe_fields,
            'problems_by_qso': problems,
            'malformed_qsos': malformed,
        }
        with open(path, "w", encoding=ADIF_ENCODING) as wfd:
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
    Write an array of QSOs to an ADIF file stream with an ADIF compatible header.
    """
    print("Created by {} version {} on {}".format(
        __PROGRAM__, __VERSION__, datetime.utcnow()), file=stream)
    adif_write_field(stream, "adif_ver", __STANDARD__)
    adif_write_field(stream, "programid", __PROGRAM__)
    adif_write_field(stream, "programversion", __VERSION__)
    adif_write_field(stream, "created_timestamp",
                     "{:%Y%m%d %H%M%S}".format(datetime.utcnow()))
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

    This is complicated by the fact that ADIF files are defined to be
    ascii only but nobody follows that convention, so they may be latin-1,
    windows cp1282, or unicode UTF-8 encoded.
    """
    try:
        with open(path, encoding=ADIF_ENCODING) as adif_file:
            adif_string = adif_file.read()
    except ValueError:
        logging.warning("%s: failed to read using latin-1 encoding, retrying as unicode", path)
        with open(path, encoding="utf-8") as adif_file:
            adif_string = adif_file.read()
    return adif_io.read_from_string(adif_string)


def read_adif_files(paths):
    """
    Read in all ADIF records from the following files
    """
    qsos = []
    malformed = []
    for path in paths:
        filename = os.path.basename(path)
        raw, _adif_header = read_adif_file(path)
        for qso in raw:
            try:
                qsos.append(fixup_qso(qso, filename))
            except QSOError as err:
                logging.warning("Ignoring QSO: %s", err.args[0])
                malformed.append(err.args[1])
    return qsos, malformed


def filter_meta_fields(qsos, critical_only):
    """
    Remove any meta fields we created like _UNREFERENCED or _FILENAME.
    If critical, filter out everything other than critical fields.
    If critical, filter out gridsquare as well because of the MH4 vs MH6/8 differences.
    """
    if critical_only:
        fields = [field for field in FIELD_ORDER if field not in ["GRIDSQUARE", "NAME", "COMMENT"]]
        qsos = [{key: val for key, val in qso.items() if key in fields} for qso in qsos]
    else:
        qsos = [{key: val for key, val in qso.items() if key[0] != "_"} for qso in qsos]
    return qsos


def dump_qso_comparison(test_qsos, reference_qsos, compare_critical):
    """
    Compare the differences between qsos and previous run.
    """
    reference_qsos = filter_meta_fields(reference_qsos, compare_critical)
    test_qsos = filter_meta_fields(test_qsos, compare_critical)
    reference_only = [qso for qso in reference_qsos if qso not in test_qsos]
    test_only = [qso for qso in test_qsos if qso not in reference_qsos]
    # XXX this is just a hack for now, we should really come up with a better encoding
    with open("compare-1.json", "w", encoding=ADIF_ENCODING) as cfd:
        json.dump(test_only, cfd, indent=4, sort_keys=True)
    with open("compare-2.json", "w", encoding=ADIF_ENCODING) as cfd:
        json.dump(reference_only, cfd, indent=4, sort_keys=True)


def main():
    """
    Load ADIF files, clean each qso individually produce output
    """
    parser = argparse.ArgumentParser(
        description="Merge ADIF files",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--problems', '-p', type=str,
                        help="Intermediate problem output .json")
    parser.add_argument('--compare', '-c', type=str,
                        help="Merge ADIF files and only compare against previous run")
    parser.add_argument('--compare-critical', '-C', action='store_true',
                        help="When doing a comparison, only compare critical QSO fields")
    parser.add_argument('--output', '-o', type=str, default="qso_merged.adif",
                        help="Merged log output .adif")
    parser.add_argument('--minimal', '-m', action='store_true',
                        help="Only output important fields")
    parser.add_argument('--merge-window', type=int, default=MERGE_WINDOW,
                        help="Time window for merging discrepent log entries")
    parser.add_argument('--wsjtx-log', '-w', type=str,
                        help="WSJT-X compatible .log file")
    parser.add_argument('--log-level', type=str, default="info",
                        help="Log level for debugging")
    parser.add_argument('--version', '-v', action='version',
                        version="%(prog)s {version}".format(version=__VERSION__))
    parser.add_argument('input', type=str, nargs="+",
                        help="Input file list")
    args = parser.parse_args()

    numeric_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError("Invalid log-level: {}".format(args.log_level))
    logging.basicConfig(format='%(levelname)s: %(message)s', level=numeric_level)

    qsos, malformed = read_adif_files(args.input)
    qsos = merge_qsos(qsos, args.merge_window)

    if args.compare:
        reference_qsos, _reference_malformed = read_adif_files([args.compare])
        dump_qso_comparison(qsos, reference_qsos, args.compare_critical)
        return

    if args.problems:
        dump_problems(qsos, malformed, args.problems)

    if args.output:
        # ADIF files are supposed to be ascii, not unicode, unfortunately.
        with open(args.output, "w", encoding=ADIF_ENCODING) as adiffile:
            adif_write(adiffile, qsos, args.minimal)

    if args.wsjtx_log:
        with open(args.wsjtx_log, "w", encoding=ADIF_ENCODING) as csvfile:
            csv_write(csvfile, qsos)


if __name__ == "__main__":
    main()
