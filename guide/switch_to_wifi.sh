#!/bin/bash
# 从热点模式切回 WiFi — 在 Mini 上执行：bash /home/orangepi/switch_to_wifi.sh
# 后台执行，关闭热点后自动连 WiFi，不依赖 SSH 连接

echo orangepi | sudo -S bash -c '
cat > /tmp/switch_wifi.sh << SCRIPTEOF
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
nmcli device wifi connect polyu-NJRI-1085_5G-2 password mrs114338
sleep 2
echo DONE >> /tmp/switch_wifi.log
ip addr show wlan0 >> /tmp/switch_wifi.log
SCRIPTEOF
chmod +x /tmp/switch_wifi.sh
nohup /tmp/switch_wifi.sh > /dev/null 2>&1 &
echo "hotspot -> WiFi switch started"
'
