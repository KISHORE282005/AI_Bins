@echo off

cd /d F:\ai_projects\AI_Bins

call venv\Scripts\activate

python app.py --server.port=4004 --server.address=192.168.1.49