# Git Backup Info

## Remote
- URL: `https://github.com/QOULMPEFA/hkcrc_robot`
- PAT: `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` (Expires Aug 31, 2026)

## Backup Flow
1. PC: commit changes → `git push origin main`
2. Jetson: `git fetch origin && git reset --hard origin/main`
3. Jetson: `sudo systemctl restart robot_control_panel.service`

## Jetson Service
- Service: `robot_control_panel.service`
- Log: `/var/log/robot_control_panel.log`
- Restart: `echo <password> | sudo -S systemctl restart robot_control_panel.service`
