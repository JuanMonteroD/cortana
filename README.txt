1) Crea un archivo .env en la raiz del proyecto con:

TELEGRAM_BOT_TOKEN=8597817394:
OWNER_TELEGRAM_USER_ID=
DB_PATH=

2) Instala dependencias:
python -m pip install -r requirements.txt

3) Ejecuta:
python src/main.py

Comandos:
- /start
- /rem_add <SCHEDULE> <NOMBRE> | <MENSAJE>
- /rem_list
- /rem_on <id>
- /rem_off <id>
- /rem_del <id>
- /rem_test

SCHEDULE soportados:
- WEEKDAY@HH:MM
- WEEKEND@HH:MM
- DAYS@mon,tue,wed,thu,fri,sat,sun@HH:MM
- ONCE@YYYY-MM-DD@HH:MM
- EVERYDAY@HH:MM (para todos los dias)
