import csv
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.parser import decode_qr, InvalidQR, demo_qr

SAMPLE=(Path(__file__).parent/'fixtures/apk-sample.txt').read_text()

class ParserTests(unittest.TestCase):
    def test_apk_sample_values_and_date_are_preserved(self):
        value=decode_qr(SAMPLE)
        self.assertEqual(value['metrics']['weight'],70.0)
        self.assertEqual(value['metrics']['muscle'],53.5)
        self.assertEqual(value['metrics']['bmi'],24.1)
        self.assertEqual(value['height'],178.0)
        self.assertEqual(value['measured_at'],'2018-02-28T22:25:00'[:16])
    def test_url_preserves_signed_fields_and_hash(self):
        value=decode_qr('https://QR.ACCUNIQ.COM/accuniq?bcadata='+SAMPLE)
        encoded=decode_qr('http://qr.accuniq.com/accuniq?bcadata='+quote(SAMPLE,safe=''))
        self.assertEqual(value['qr_hash'],encoded['qr_hash'])
        self.assertEqual(value['raw_fields']['weight_control_value'],SAMPLE[350:355])
    def test_invalid_domains_formats_dates_and_numbers_rejected(self):
        invalid=[SAMPLE[:-1],SAMPLE[:-1]+'!',SAMPLE.replace('1802282225','1899282225'),
                 SAMPLE[:46]+'xxxx'+SAMPLE[50:],SAMPLE[:222]+'9999'+SAMPLE[226:],
                 'https://example.com/accuniq?bcadata='+SAMPLE,
                 'https://[bad/accuniq?bcadata='+SAMPLE,
                 'https://qr.accuniq.com/accuniq?bcadata='+SAMPLE+'&bcadata='+SAMPLE,
                 SAMPLE[:20]+'\x00'+SAMPLE[21:]]
        for value in invalid:
            with self.subTest(value=value[:30]),self.assertRaises(InvalidQR):decode_qr(value)
    def test_imperial_height_is_not_guessed(self):
        value=decode_qr(SAMPLE[:525]+'1'+SAMPLE[526:])
        self.assertEqual(value['unit'],'lb')
        self.assertIsNone(value['height'])
        self.assertEqual(value['raw_fields']['height'],'1780')

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.client=TestClient(create_app(Path(self.temp.name)/'test.sqlite3'))
        self.client.__enter__()
        self.patient=self.client.post('/api/patients',json={'name':'Deniz'}).json()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()
    def ingest(self,**kwargs):
        return self.client.post('/api/measurements/import',json=dict(text=SAMPLE,patient_id=self.patient['id'],**kwargs))
    def test_native_scanner_preview_matches_text_preview(self):
        native=self.client.post('/api/qr/scanner/preview',json={'text':SAMPLE,'format':'QR_CODE','cancelled':0})
        self.assertEqual(native.status_code,200)
        self.assertEqual(native.json(),self.client.post('/api/qr/preview',json={'text':SAMPLE}).json())
        self.assertEqual(self.client.get('/api/measurements').json()['total'],0)
    def test_native_scanner_cancelled_wrong_format_empty_never_write(self):
        for payload in ({'text':SAMPLE,'format':'QR_CODE','cancelled':1},
                        {'text':SAMPLE,'format':'CODE_128','cancelled':0},
                        {'text':'','format':'QR_CODE','cancelled':0},
                        {'text':SAMPLE,'format':'QR_CODE','cancelled':2}):
            with self.subTest(payload=payload):
                result=self.client.post('/api/qr/scanner/import',json={**payload,'patient_id':self.patient['id']})
                self.assertEqual(result.status_code,422)
        self.assertEqual(self.client.get('/api/measurements').json()['total'],0)
    def test_native_scanner_import_shares_duplicate_and_profile_protection(self):
        payload={'text':SAMPLE,'format':'QR_CODE','cancelled':0,'patient_id':self.patient['id']}
        first=self.client.post('/api/qr/scanner/import',json=payload)
        self.assertEqual(first.status_code,200)
        self.assertFalse(first.json()['duplicate'])
        self.assertTrue(self.ingest().json()['duplicate'])
        other=self.client.post('/api/patients',json={'name':'Ada'}).json()
        payload['patient_id']=other['id']
        self.assertEqual(self.client.post('/api/qr/scanner/import',json=payload).status_code,409)
    def test_dex_report_archive_and_evidence(self):
        import zipfile
        data=self.client.get('/api/analysis/dex').json()
        self.assertTrue(data['matches_apk'])
        self.assertEqual(data['class_count'],2856)
        self.assertEqual(len(data['hardware_evidence']),2)
        self.assertTrue(all(e['opcode']=='const-class' for e in data['hardware_evidence']))
        self.assertEqual(self.client.get('/api/analysis/dex/report').status_code,200)
        response=self.client.get('/api/analysis/dex/archive')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('all-bytecode.txt',archive.namelist())
            self.assertIn('java/kr/co/bca/MainActivity.java',archive.namelist())
    def test_preview_does_not_write_and_import_is_idempotent(self):
        preview=self.client.post('/api/qr/preview',json={'text':SAMPLE})
        self.assertEqual(preview.status_code,200)
        self.assertEqual(self.client.get('/api/measurements').json()['total'],0)
        first=self.ingest().json();second=self.ingest().json()
        self.assertFalse(first['duplicate']);self.assertTrue(second['duplicate'])
        self.assertEqual(first['measurement']['id'],second['measurement']['id'])
        self.assertEqual(self.client.get('/api/measurements').json()['total'],1)
    def test_invalid_qr_and_unknown_profile_never_write(self):
        self.assertEqual(self.client.post('/api/measurements/import',json={'text':'bad','patient_id':self.patient['id']}).status_code,422)
        self.assertEqual(self.client.post('/api/measurements/import',json={'text':SAMPLE,'patient_id':'missing'}).status_code,404)
        self.assertEqual(self.client.get('/api/measurements').json()['total'],0)
    def test_same_qr_cannot_be_reassigned_to_another_profile(self):
        self.ingest()
        other=self.client.post('/api/patients',json={'name':'Ada'}).json()
        self.assertEqual(self.client.post('/api/measurements/import',json={'text':SAMPLE,'patient_id':other['id']}).status_code,409)
        self.assertEqual(self.client.get('/api/measurements',params={'patient_id':other['id']}).json()['total'],0)
    def test_demo_is_tagged_idempotent_and_cannot_accept_real_data(self):
        demo=self.client.post('/api/demo').json()
        self.assertFalse(self.client.post('/api/demo').json()['created'])
        rows=self.client.get('/api/measurements',params={'patient_id':demo['patient_id']}).json()['items']
        self.assertEqual(len(rows),10)
        self.assertTrue(all(r['source']=='demo' for r in rows))
        self.assertEqual(self.client.post('/api/measurements/import',json={'text':SAMPLE,'patient_id':demo['patient_id']}).status_code,409)
        self.ingest()
        self.client.delete('/api/demo')
        self.assertEqual(self.client.get('/api/measurements').json()['total'],1)
    def test_export_escapes_spreadsheet_formula_and_filters_profile(self):
        formula=self.client.post('/api/patients',json={'name':'=SUM(A1)'}).json()
        self.client.post('/api/measurements/import',json={'text':SAMPLE,'patient_id':formula['id']})
        response=self.client.get('/api/export.csv',params={'patient_id':formula['id']})
        rows=list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
        self.assertEqual(rows[1][0],"'=SUM(A1)")
        self.assertEqual(len(rows),2)
        self.assertEqual(len(list(csv.reader(io.StringIO(self.client.get('/api/export.csv',params={'patient_id':self.patient['id']}).text)))),1)
    def test_delete_and_persistence(self):
        row=self.ingest().json()['measurement']
        self.assertEqual(self.client.get('/api/measurements/'+row['id']).json()['metrics']['weight'],70)
        self.assertEqual(self.client.delete('/api/measurements/'+row['id']).status_code,200)
        self.assertEqual(self.client.get('/api/measurements/'+row['id']).status_code,404)
    def test_reference_and_device_do_not_claim_live_connection(self):
        reference=self.client.get('/api/reference').json()
        self.assertEqual(len(reference['api']),403)
        self.assertEqual(reference['qr_fields'][-1]['end'],527)
        device=self.client.get('/api/device').json()
        self.assertFalse(device['connected']);self.assertFalse(device['protocol_verified'])
        self.assertTrue(device['qr_ready'])
    def test_profile_validation_and_pagination(self):
        self.assertEqual(self.client.post('/api/patients',json={'name':'   '}).status_code,422)
        self.client.post('/api/demo')
        rows=self.client.get('/api/measurements',params={'limit':3,'offset':2}).json()
        self.assertEqual(rows['total'],10);self.assertEqual(len(rows['items']),3)
        self.assertEqual(self.client.get('/api/measurements',params={'limit':1001}).status_code,422)

if __name__=='__main__':unittest.main()
