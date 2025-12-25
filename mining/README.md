# Leo XMRig Mining Setup

- xmrig-live.json: Live config (sanitized—real wallet in running version)
- xmrig.service: Systemd unit (symlinked to /etc/systemd/system/)

To update:
1. Edit file here (repo)
2. sudo cp xmrig-live.json /opt/xmrig/xmrig.json
3. sudo systemctl daemon-reload
4. sudo systemctl restart xmrig.service

Huge pages: Check `cat /proc/meminfo | grep Huge`
Happy hashing! 😺
