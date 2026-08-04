@echo off
c:
cd "c:\Program Files\Microvirt\MEmu"
adb shell "cd /data/local/tmp/ && ./frida-server"

