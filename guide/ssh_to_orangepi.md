## SSH & Env
- Host: 192.168.1.101:22 | User: orangepi | Pass: orangepi
- Workspace: `/home/orangepi/pr_mini`
- AnyDesk: GUI available if needed.

## SSH Config (~/.ssh/config)

ssh-skill 脚本依赖注释元数据中的 `# password:` 字段，缺少会导致 "必须提供 password 或 key_file" 错误。