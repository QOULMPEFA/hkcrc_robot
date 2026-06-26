# 客户问题解决步骤

## 机器人网站显示"未连接"

### 第一步：确保已连接机器人 WiFi

电脑/手机先连接热点：

- WiFi 名称：**PipeRobot**
- 密码：**pipemaker**

### 第二步：打开命令行

**Windows 用户：**
按 `Win + R`，输入 `powershell`，回车。

**Mac 用户：**
打开 `终端`（Terminal）应用。

### 第三步：SSH 登录机器人

在命令行中输入：

```
ssh orangepi@192.168.12.1
```

按回车后会提示输入密码，输入（不会显示，盲打即可）：

```
orangepi
```

看到 `orangepi@orangepizero2w:~$` 表示登录成功。

### 第四步：重启控制服务

```bash
echo orangepi | sudo -S systemctl restart robot_control_panel
```

等待约 10 秒后刷新浏览器页面 `http://192.168.12.1:6080`。

### 如果上面方法不行，直接重启机器人

拔掉机器人电源，等 5 秒，重新插电。等待约 30~60 秒后，浏览器刷新页面即可。

---

## 摄像头无画面

1. 确认 USB 摄像头已插入机器人
2. 按上面第四步重启服务

---

## Windows 提示 "ssh 不是内部或外部命令"

部分旧版 Windows 10 未安装 OpenSSH，需要启用：

1. 打开 `设置` → `应用` → `可选功能`
2. 点击 `添加功能`
3. 搜索 `OpenSSH 客户端`，点击安装
4. 重新打开 PowerShell 即可使用 ssh
