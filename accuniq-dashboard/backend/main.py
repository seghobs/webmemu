from contextlib import asynccontextmanager, contextmanager
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, Field, field_validator
from .parser import decode_qr, demo_qr, InvalidQR, SCHEMA

REFERENCE = Path(__file__).parent/'reference'

class ProfileInput(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    note: str = Field(default='',max_length=300)
    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        value=value.strip()
        if not value or any(ord(c)<32 for c in value): raise ValueError('Geçerli bir profil adı girin.')
        return value

class QRInput(BaseModel):
    text: str = Field(min_length=1,max_length=4096)

class ImportInput(QRInput):
    patient_id: str = Field(min_length=1,max_length=40)
    note: str = Field(default='',max_length=300)

class ScannerResult(BaseModel):
    """BarcodeScanner.onActivityResult callback contract recovered from DEX."""
    text: str = Field(default='',max_length=4096)
    format: str = Field(default='',max_length=40)
    cancelled: Literal[0,1] = 0

class ScannerImport(ScannerResult):
    patient_id: str = Field(min_length=1,max_length=40)
    note: str = Field(default='',max_length=300)

def create_app(database: str | Path | None = None) -> FastAPI:
    db_path=Path(database or os.environ.get('ACCUNIQ_DB_PATH',Path(__file__).parent/'data/studio.sqlite3'))
    @contextmanager
    def db():
        conn=sqlite3.connect(db_path,timeout=10)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn: yield conn
        finally: conn.close()

    @asynccontextmanager
    async def lifespan(app):
        db_path.parent.mkdir(parents=True,exist_ok=True)
        with db() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS patients (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS measurements (
                    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
                    qr_hash TEXT UNIQUE NOT NULL, measured_at TEXT NOT NULL,
                    data TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
                    imported_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS measurement_patient_date ON measurements(patient_id,measured_at);
            ''')
        yield

    app=FastAPI(title='ACCUNIQ Studio · Yerel API',version='1.0.0',lifespan=lifespan)
    def profile_row(row):
        result=dict(row)
        result['is_demo']=bool(result['is_demo'])
        return result
    def measurement_row(row):
        result=json.loads(row['data'])
        result.update(id=row['id'],patient_id=row['patient_id'],patient_name=row['patient_name'],
                      note=row['note'],source=row['source'],imported_at=row['imported_at'])
        result.pop('qr_hash',None)
        return result
    def select_measurement(conn,identifier):
        row=conn.execute('SELECT m.*,p.name AS patient_name FROM measurements m JOIN patients p ON p.id=m.patient_id WHERE m.id=?',(identifier,)).fetchone()
        if not row: raise HTTPException(404,'Ölçüm bulunamadı.')
        return measurement_row(row)
    def parse(text):
        try: return decode_qr(text)
        except InvalidQR as exc: raise HTTPException(422,str(exc)) from exc

    @app.get('/api/health')
    def health():
        with db() as conn: conn.execute('SELECT 1').fetchone()
        return dict(status='ok',storage='sqlite',mode='local',device_model='BC380')

    @app.get('/api/patients')
    def patients():
        with db() as conn:
            rows=conn.execute('''SELECT p.*,COUNT(m.id) AS measurement_count,MAX(m.measured_at) AS last_measurement
                                FROM patients p LEFT JOIN measurements m ON p.id=m.patient_id
                                GROUP BY p.id ORDER BY p.is_demo,p.name COLLATE NOCASE''').fetchall()
            return [profile_row(r) for r in rows]

    @app.post('/api/patients',status_code=201)
    def add_patient(data: ProfileInput):
        identifier=str(uuid.uuid4())
        with db() as conn:
            conn.execute('INSERT INTO patients(id,name,note,created_at) VALUES(?,?,?,?)',
                         (identifier,data.name,data.note,datetime.now(timezone.utc).isoformat()))
            return dict(id=identifier,name=data.name,note=data.note,is_demo=False,measurement_count=0,last_measurement=None)

    @app.post('/api/qr/preview')
    def preview(data: QRInput):
        result=parse(data.text)
        with db() as conn:
            existing=conn.execute('SELECT id FROM measurements WHERE qr_hash=?',(result['qr_hash'],)).fetchone()
        result.pop('qr_hash')
        return dict(measurement=result,duplicate_id=existing['id'] if existing else None)

    @app.post('/api/measurements/import')
    def import_measurement(data: ImportInput):
        decoded=parse(data.text)
        with db() as conn:
            patient=conn.execute('SELECT * FROM patients WHERE id=?',(data.patient_id,)).fetchone()
            if not patient: raise HTTPException(404,'Önce bir profil seçin veya oluşturun.')
            if patient['is_demo']: raise HTTPException(409,'Gerçek ölçüm için örnek profil yerine yeni profil oluşturun.')
            existing=conn.execute('SELECT id,patient_id FROM measurements WHERE qr_hash=?',(decoded['qr_hash'],)).fetchone()
            if existing:
                if existing['patient_id'] != data.patient_id: raise HTTPException(409,'Bu QR başka bir profile kayıtlı.')
                return dict(duplicate=True,measurement=select_measurement(conn,existing['id']))
            identifier=str(uuid.uuid4())
            try:
                conn.execute('INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?)',
                             (identifier,data.patient_id,decoded['qr_hash'],decoded['measured_at'],json.dumps(decoded),
                              data.note,'qr',datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                raise HTTPException(409,'Bu QR aynı anda başka bir işlemde kaydedildi; listeyi yenileyin.')
            return dict(duplicate=False,measurement=select_measurement(conn,identifier))

    @app.get('/api/measurements')
    def measurements(patient_id: str | None = None,limit: int = Query(200,ge=1,le=1000),offset: int = Query(0,ge=0)):
        where=' WHERE m.patient_id=?' if patient_id else ''
        parameters=[patient_id] if patient_id else []
        with db() as conn:
            total=conn.execute('SELECT COUNT(*) FROM measurements m'+where,parameters).fetchone()[0]
            rows=conn.execute('SELECT m.*,p.name AS patient_name FROM measurements m JOIN patients p ON p.id=m.patient_id'+where+
                              ' ORDER BY m.measured_at DESC,m.id DESC LIMIT ? OFFSET ?',parameters+[limit,offset]).fetchall()
            return dict(items=[measurement_row(r) for r in rows],total=total)

    def scanner_text(data: ScannerResult):
        if data.cancelled: raise HTTPException(422,'QR taraması iptal edildi; ölçüm kaydedilmedi.')
        if data.format != 'QR_CODE': raise HTTPException(422,'ACCUNIQ ölçümü için QR_CODE biçimi gerekiyor.')
        if not data.text: raise HTTPException(422,'Tarayıcı sonucu boş.')
        return data.text

    @app.post('/api/qr/scanner/preview')
    def scanner_preview(data: ScannerResult):
        return preview(QRInput(text=scanner_text(data)))

    @app.post('/api/qr/scanner/import')
    def scanner_import(data: ScannerImport):
        return import_measurement(ImportInput(text=scanner_text(data),patient_id=data.patient_id,note=data.note))

    @app.get('/api/analysis/dex')
    def dex_analysis():
        return json.loads((REFERENCE/'dex-analysis.json').read_text(encoding='utf-8'))

    @app.get('/api/analysis/dex/report')
    def dex_report():
        return FileResponse(REFERENCE/'DEX-ANALIZ-RAPORU.md',media_type='text/markdown; charset=utf-8',filename='DEX-ANALIZ-RAPORU.md')

    @app.get('/api/analysis/dex/archive')
    def dex_archive():
        return FileResponse(REFERENCE/'dex-inventory.zip',media_type='application/zip',filename='accuniq-dex-inventory.zip')

    @app.get('/api/measurements/{identifier}')
    def measurement(identifier: str):
        with db() as conn: return select_measurement(conn,identifier)

    @app.delete('/api/measurements/{identifier}')
    def delete_measurement(identifier: str):
        with db() as conn:
            select_measurement(conn,identifier)
            conn.execute('DELETE FROM measurements WHERE id=?',(identifier,))
        return dict(deleted=True)

    @app.get('/api/export.csv')
    def export(patient_id: str | None = None):
        stream=io.StringIO(newline='')
        writer=csv.writer(stream)
        writer.writerow(['Profil','Tarih','Kaynak','Birim','Kilo','Kas','Yağ yüzdesi','BMI','Boy cm'])
        where=' WHERE m.patient_id=?' if patient_id else ''
        with db() as conn:
            rows=conn.execute('SELECT m.*,p.name AS patient_name FROM measurements m JOIN patients p ON p.id=m.patient_id'+where+
                              ' ORDER BY measured_at',([patient_id] if patient_id else [])).fetchall()
        for row in rows:
            value=measurement_row(row)
            label=value['patient_name']
            if label.lstrip().startswith(('=','+','-','@')):label="'"+label
            writer.writerow([label,value['measured_at'],value['source'],value['unit'],value['metrics']['weight'],
                             value['metrics']['muscle'],value['metrics']['fat_percent'],value['metrics']['bmi'],value['height']])
        return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="accuniq-measurements.csv"'})

    @app.get('/api/backup')
    def backup():
        with db() as conn:
            profiles=[profile_row(r) for r in conn.execute('SELECT * FROM patients')]
            rows=conn.execute('SELECT m.*,p.name AS patient_name FROM measurements m JOIN patients p ON p.id=m.patient_id').fetchall()
        return Response(json.dumps(dict(version=1,patients=profiles,measurements=[measurement_row(r) for r in rows]),ensure_ascii=False,indent=2),
                        media_type='application/json',headers={'Content-Disposition':'attachment; filename="accuniq-backup.json"'})

    @app.post('/api/demo')
    def demo():
        with db() as conn:
            existing=conn.execute('SELECT id FROM patients WHERE is_demo=1').fetchone()
            if existing: return dict(patient_id=existing['id'],created=False)
            identifier=str(uuid.uuid4())
            conn.execute('INSERT INTO patients VALUES(?,?,?,?,1)',(identifier,'Örnek profil','Sentetik gösterim verisi',datetime.now(timezone.utc).isoformat()))
            end=datetime.now().replace(hour=9,minute=30,second=0,microsecond=0)
            for index in range(10):
                decoded=decode_qr(demo_qr(end-timedelta(days=(9-index)*7),79.5-index*.34,33.4+index*.16,22.1-index*.32))
                conn.execute('INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?)',
                             (str(uuid.uuid4()),identifier,decoded['qr_hash'],decoded['measured_at'],json.dumps(decoded),
                              'Sentetik veri; cihaz ölçümü değildir.','demo',datetime.now(timezone.utc).isoformat()))
        return dict(patient_id=identifier,created=True)

    @app.delete('/api/demo')
    def remove_demo():
        with db() as conn:
            conn.execute("DELETE FROM measurements WHERE source='demo'")
            conn.execute('DELETE FROM patients WHERE is_demo=1')
        return dict(deleted=True)

    @app.get('/api/device')
    def device():
        from .device import device_status
        return device_status()

    @app.get('/api/reference')
    def reference():
        with (REFERENCE/'api-unique.csv').open(encoding='utf-8-sig') as file:
            rows=list(csv.DictReader(file))
        return dict(base_url='https://bca-app2.accuniq.com',app_version='1.2.29',api=rows,qr_fields=SCHEMA,
                    note='APK kaynak tanımları; canlı sunucu doğrulaması veya cihaz USB/Bluetooth protokolü değildir.')
    return app

app=create_app()
