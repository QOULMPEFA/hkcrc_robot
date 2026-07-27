## SSH & Env
Try local ip 1st
- Host: 192.168.50.227:22 | User: orangepi | Pass: orangepi 
- Host: frp-fit.com:35500 | User: orangepi | Pass: orangepi
- Workspace: `/home/orangepi/pr_mini`
- AnyDesk: GUI available if needed.
摄像头未连接
->6080

## SSH Config (~/.ssh/config)
ssh-skill 脚本依赖注释元数据中的 `# password:` 字段，缺少会导致 "必须提供 password 或 key_file" 错误。