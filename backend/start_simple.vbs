CreateObject("WScript.Shell").Run "D:\python软件\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level info", 1, False
