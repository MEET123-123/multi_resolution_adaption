"""Audit exported records against the retained original detail responses."""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import pathlib

from crawl import APP_CATEGORIES
from icon_assets import export_lock, image_info


def validate(folder):
    with export_lock(folder):
        return validate_locked(folder)


def validate_locked(folder):
    seen = set()
    categories = collections.Counter()
    short = []
    errors = []
    records = []
    verified_icons = 0
    with (folder / 'apps.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            ident = record['app_id']
            if ident in seen:
                errors.append(ident + ': duplicate ID')
            seen.add(ident)
            if record['country'] != 'CN' or record['locale'] != 'zh_CN':
                errors.append(ident + ': wrong locale')
            if record['ctype'] != 0 or record['category'] not in APP_CATEGORIES:
                errors.append(ident + ': not a positively classified regular app')
            if not record['description']:
                errors.append(ident + ': missing description')
            if len(record['description']) != record['description_length']:
                errors.append(ident + ': incorrect description length')
            with gzip.open(folder / 'raw/details' / (ident + '.json.gz'), 'rt', encoding='utf-8') as raw:
                source = json.load(raw)
            original = [item['appIntro'] for card in source.get('layoutData', [])
                        if card.get('layoutName') == 'detailappintrocard'
                        for item in card.get('dataList', [])]
            if original != [record['description_raw']]:
                errors.append(ident + ': original description does not match export')
            digest = hashlib.sha256(record['description_raw'].encode()).hexdigest()
            if digest != record['description_sha256']:
                errors.append(ident + ': description hash mismatch')
            if record.get('icon_status') == 'done':
                icon_file = (folder / record['icon_local_path']).resolve()
                if not icon_file.is_relative_to((folder / 'icons').resolve()) or not icon_file.is_file():
                    errors.append(ident + ': local icon is missing or outside icons directory')
                else:
                    content = icon_file.read_bytes()
                    if hashlib.sha256(content).hexdigest() != record['icon_sha256']:
                        errors.append(ident + ': icon hash mismatch')
                    if len(content) != record['icon_size_bytes']:
                        errors.append(ident + ': icon size mismatch')
                    try:
                        _, width, height = image_info(content)
                        if width != record['icon_width'] or height != record['icon_height']:
                            errors.append(ident + ': icon dimensions mismatch')
                    except Exception as error:
                        errors.append(ident + ': invalid icon: ' + str(error))
                    verified_icons += 1
            if record['description_length'] < 100:
                short.append({'app_id': ident, 'name': record['name'], 'length': record['description_length']})
            categories[record['category']] += 1
            records.append(record)
    with (folder / 'apps.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_records = list(csv.DictReader(stream))
    if len(csv_records) != len(records):
        errors.append('CSV and JSONL row counts differ')
    else:
        for csv_row, record in zip(csv_records, records):
            if csv_row['app_id'] != record['app_id'] or csv_row['description_raw'] != record['description_raw']:
                errors.append(record['app_id'] + ': CSV description mismatch')
            if csv_row.get('icon_local_path', '') != (record.get('icon_local_path') or ''):
                errors.append(record['app_id'] + ': CSV icon path mismatch')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['exported_app_count'] != len(records):
        errors.append('Manifest and data counts differ')
    report = {'passed': not errors, 'checked_records': len(records),
              'verified_local_icons': verified_icons,
              'icons_not_yet_available_in_export': len(records) - verified_icons,
              'raw_description_verified_records': len(records), 'categories': dict(categories),
              'official_descriptions_under_100_chars': len(short), 'short_description_examples': short[:30],
              'errors': errors}
    (folder / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', default='artifacts/huawei_appgallery/data')
    validate(pathlib.Path(parser.parse_args().folder))
