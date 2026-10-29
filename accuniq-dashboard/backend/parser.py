"""Decode the fixed 527-character format recovered from ACCUNIQ 1.2.29.

This is a QR decoder, not an implementation of the BC380 USB/Bluetooth protocol.
Imperial height has two encodings in the original app: it is kept raw rather
than guessing a conversion. All original fields are retained for inspection.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote

SCHEMA = json.loads((Path(__file__).parent/'reference/qr-schema.json').read_text())

class InvalidQR(ValueError):
    pass

def decode_qr(text: str) -> dict:
    text = text.strip()
    if len(text) > 4096:
        raise InvalidQR('QR verisi çok uzun.')
    if '://' in text:
        try:
            parsed = urlsplit(text)
        except ValueError as exc:
            raise InvalidQR('QR bağlantısının adres biçimi geçersiz.') from exc
        if parsed.scheme not in ('http','https') or parsed.netloc.lower() != 'qr.accuniq.com' or parsed.path != '/accuniq':
            raise InvalidQR('Bu bağlantı bir ACCUNIQ ölçüm QR kodu değil.')
        # unquote instead of parse_qs: signed numeric fields contain literal +.
        parts = parsed.query.split('&')
        values = [p[len('bcadata='):] for p in parts if p.startswith('bcadata=')]
        if len(values) != 1 or len(parts) != 1 or parsed.fragment:
            raise InvalidQR('QR bağlantısı yalnızca bir bcadata alanı içermeli.')
        text = unquote(values[0])
    if len(text) != 527 or not all(32<=ord(c)<=126 for c in text) or text[-1] != '@':
        raise InvalidQR('527 karakterlik, @ ile biten ACCUNIQ QR verisi gerekli. Bu QR sürümü desteklenmiyor.')
    raw = {f['name']: text[f['start']:f['end']] for f in SCHEMA}
    if raw['protocol_command'] != 'Mg' or not re.fullmatch(r'[A-Za-z]{2}\d{4}',raw['ap_version']):
        raise InvalidQR('QR protokolü veya sürüm başlığı tanınmadı.')
    try:
        stamp = datetime.strptime(raw['received_date'], '%y%m%d%H%M')
    except ValueError as exc:
        raise InvalidQR('Ölçüm tarihi geçersiz.') from exc
    required = ('sex','age','height','weight','muscle_quantity','mbf_quantity','pbf_rate',
                'bmi','tbw_quantity','protein_quantity','mineral_quantity','bmr','calory','IsKgLb')
    for key in required:
        if not raw[key].isdigit():
            raise InvalidQR(f'QR içindeki {key} alanı sayısal değil.')
    flag = int(raw['IsKgLb'])
    if flag not in (0,1,3):
        raise InvalidQR('Birim kodu desteklenmiyor. Cihazdan kg/cm biçiminde QR alın.')
    if raw['sex'] not in ('1','2') or not 1 <= int(raw['age']) <= 120:
        raise InvalidQR('Yaş veya cinsiyet alanı geçersiz.')
    warnings = ['QR bütünlük imzası doğrulanmadı; alan biçimleri ve temel aralıklar kontrol edildi.']
    if flag != 0:
        warnings.append('Imperial boy kodu belirsiz olduğundan boy dönüştürülmedi. Kg/cm QR ile boy da görüntülenir.')
    metrics = {label: round(int(raw[field])/10,1) for label,field in {
        'weight':'weight','muscle':'muscle_quantity','fat_mass':'mbf_quantity','fat_percent':'pbf_rate',
        'bmi':'bmi','water':'tbw_quantity','protein':'protein_quantity','mineral':'mineral_quantity'}.items()}
    if not (0 < metrics['weight'] <= (300 if flag == 0 else 660)):
        raise InvalidQR('Kilo alanı beklenen aralıkta değil.')
    if not 0 <= metrics['fat_percent'] <= 100 or not 0 < metrics['bmi'] <= 100:
        raise InvalidQR('Yağ yüzdesi veya BMI alanı beklenen aralıkta değil.')
    if flag == 0:
        height = int(raw['height'])/10
        if not 30 <= height <= 260:
            raise InvalidQR('Boy alanı beklenen aralıkta değil.')
        if any(metrics[k] > metrics['weight'] for k in ('muscle','fat_mass','water','protein','mineral')):
            raise InvalidQR('Bileşen kütlesi toplam kilodan büyük olamaz.')
    else:
        height = None
    return dict(qr_hash=hashlib.sha256(text.encode()).hexdigest(),measured_at=stamp.isoformat(timespec='minutes'),
                device_subject=raw['id_number'].strip(),age=int(raw['age']),sex=raw['sex'],height=height,
                unit='kg' if flag == 0 else 'lb',unit_code=flag,metrics=metrics,
                bmr=int(raw['bmr']),calories=int(raw['calory']),raw_fields=raw,warnings=warnings)

def demo_qr(date: datetime, weight=76.4, muscle=34.8, fat_percent=19.2) -> str:
    """Explicitly synthetic sample, never presented as a device measurement."""
    chars = list('0'*526+'@')
    values = dict(protocol_command='Mg',ap_version='KR0200',received_date=date.strftime('%y%m%d%H%M'),
                  id_number='DEMO-BC380'.ljust(20),sex='1',age='032',height='1780',IsKgLb='0',
                  weight=f'{round(weight*10):04}',muscle_quantity=f'{round(muscle*10):04}',
                  mbf_quantity=f'{round(weight*fat_percent/10):04}',pbf_rate=f'{round(fat_percent*10):04}',
                  bmi=f'{round(weight/1.78**2*10):04}',tbw_quantity=f'{round(weight*.56*10):04}',
                  protein_quantity='0124',mineral_quantity='0036',bmr='1720',calory='2460',end_mark='@')
    for field in SCHEMA:
        if field['name'] in values:
            chars[field['start']:field['end']] = values[field['name']]
    return ''.join(chars)
