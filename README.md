# SensorPush and Zabbix integration

Query SensorPush sensor data for Zabbix NMS integration.  Very beta!

Early alpha!

# Configuration
TODO
- install required modules (see requirements.txt)

pip3 install -r requirements.txt

- run configuration.  Supply your SensorPush email and password, and the address of your Zabbix server

./spaz.py configure

- verify connectivity

./spaz.py status

This will verify authentication and list available sensors

- Configure sensors

./spaz.py configsensor

- Configure Zabbix

TBD

- Run data collectioin/transfer

./spaz.py getzdata

This can be run via cron.  SensorPush requests that queries be run no more frequently than once per minute, so once every five is recommended.  The script will backfill any SensorPush data collected since the last time the script was run.
