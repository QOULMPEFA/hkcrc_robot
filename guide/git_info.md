# Git Backup Info

## Remote
- URL: `https://github.com/QOULMPEFA/hkcrc_robot`
- PAT: `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` (Expires on Tue, Sep 8 2026)

## Backup Flow
1. PC: commit changes → `git push origin main`
2. PC: `git tag -a vX.Y -m "vX.Y — <summary>" && git push --tags`
3. Orange Pi: `git fetch origin && git reset --hard origin/main`
4. Orange Pi: `echo orangepi | sudo -S systemctl restart robot_control_panel.service`
5. Jetson: `git fetch origin && git reset --hard origin/main`
6. Jetson: `sudo systemctl restart robot_control_panel.service`

## Version Tags
- 格式：`v<主>.<次>`，如 v1.0, v1.1, v1.2 ...
- 每次备份必须递增版本号并打 tag
- Tag 信息简要描述本次变更

## Orange Pi Zero 2W (管道机器人)
- SSH: `192.168.1.112:22` | User: `orangepi` | Pass: `orangepi`
- Workspace: `/home/orangepi/pr_mini`
- Service: `robot_control_panel.service`
- Sudo password: `orangepi`
- Active modules: camera, motor, lift (pipe robot preset)
- Web GUI: `http://192.168.1.112:6080`

## Jetson (全尺寸机器人)
- Service: `robot_control_panel.service`
- Restart: `echo <password> | sudo -S systemctl restart robot_control_panel.service`
