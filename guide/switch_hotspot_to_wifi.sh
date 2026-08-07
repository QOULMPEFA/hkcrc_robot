#!/bin/bash
# 从热点模式切回 WiFi — 单条命令，后台执行，不怕 SSH 断开
# 使用前替换第 15 行的 WiFi 名称和密码
# 用法：在 Mini 终端上整条粘贴执行

echo orangepi | sudo -S bash -c '
cat > /tmp/switch_wifi.sh << "SCRIPTEOF"
#!/bin/bash
exec > /tmp/switch_wifi.log 2>&1
systemctl stop pipe_ap.service 2>/dev/null || true
pkill -9 create_ap hostapd dnsmasq 2>/dev/null || true
sleep 1
ip link set wlan0 down
iw dev wlan0 set type managed
ip link set wlan0 up
sed -i "/unmanaged-devices/d" /etc/NetworkManager/NetworkManager.conf
systemctl restart NetworkManager
sleep 3
nmcli device wifi connect "polyu-NJRI-1085_5G-2" password "mrs114338"
sleep 2
echo "DONE" >> /tmp/switch_wifi.log
ip addr show wlan0 >> /tmp/switch_wifi.log
SCRIPTEOF
chmod +x /tmp/switch_wifi.sh
nohup /tmp/switch_wifi.sh > /dev/null 2>&1 &
echo "switch to WiFi started in background"
'
