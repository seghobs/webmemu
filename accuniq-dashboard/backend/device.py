"""Read-only hardware discovery. No guessed commands are sent to the BC380."""
def device_status():
    try:
        from serial.tools import list_ports
        ports=[dict(name=p.device,description=p.description) for p in list_ports.comports()]
        serial_available=True
    except ImportError:
        ports=[]
        serial_available=False
    return dict(model='ACCUNIQ BC380',connected=False,protocol_verified=False,ports=ports,
                serial_available=serial_available,qr_ready=True,
                verified_data_path='qr',analysis_url='/api/analysis/dex',
                scanner_contract=dict(fields=['text','format','cancelled'],format='QR_CODE',
                                      preview='/api/qr/scanner/preview',import_url='/api/qr/scanner/import'),
                transports=[dict(id='qr',status='ready',message='QR metni, kamera veya QR görseli ile ölçüm aktarımı.'),
                            dict(id='usb',status='pending',message='Cihaz bağlantısı ve aktarım protokolü doğrulanmalı.'),
                            dict(id='bluetooth',status='pending',message='Dongle türü ve iletişim protokolü doğrulanmalı.')])
