# gaslimit.pics
Follow validators signaling different gas limits.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp /path/to/.pyxatu_config.json .   # xatu ClickHouse creds, gitignored
./update.sh                         # regenerate + commit + push
```
