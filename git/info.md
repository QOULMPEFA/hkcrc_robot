# Git Backup Info

## Remote
- URL: `https://github.com/QOULMPEFA/hkcrc_robot`
- PAT: `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` (Expires on Tue, Sep 8 2026)

## Backup Flow
1. PC: commit changes → `git push origin main`
2. PC: `git tag -a vX.Y -m "vX.Y — <summary>" && git push --tags`
3. Jetson: `git fetch origin && git reset --hard origin/main`
4. Jetson: `sudo systemctl restart robot_control_panel.service`

## Version Tags
- 格式：`v<主>.<次>`，如 v1.0, v1.1, v1.2 ...
- 每次备份必须递增版本号并打 tag
- Tag 信息简要描述本次变更

## Jetson Service
- Service: `robot_control_panel.service`
- Log: `/var/log/robot_control_panel.log`
- Restart: `echo <password> | sudo -S systemctl restart robot_control_panel.service`
