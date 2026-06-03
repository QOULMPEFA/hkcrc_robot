/**
 * @File      : ugv_bridge.cpp
 * @Brief     : TCP bridge between ugv_sdk CAN and robot_control Python layer.
 *              Talks CAN to Ranger Mini 3.0, exposes JSON-line TCP on :9100.
 *
 * Build      : cd ugv_sdk && mkdir -p build && cd build && cmake .. && make -j4
 *              g++ -std=c++17 -I../include -I/usr/include ugv_bridge.cpp
 *                  -L./lib -lugv_sdk -lpthread -o ugv_bridge
 * Run        : ./ugv_bridge can0
 * Test       : nc 127.0.0.1 9100
 *
 * Protocol (commands, client → bridge):
 *   {"cmd":"velocity","linear":0.5,"angular":0.0}
 *   {"cmd":"estop"}
 *   {"cmd":"enable"}
 *
 * Protocol (state, bridge → client, ~20 Hz):
 *   JSON object with fields from RangerCoreState / ActuatorState / SensorState.
 */

#include <csignal>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include "ugv_sdk/mobile_robot/ranger_robot.hpp"

using namespace westonrobot;

// ── TCP constants ──────────────────────────────────────────────────────────────
#define BRIDGE_PORT      9100
#define BACKLOG          1
#define STATE_INTERVAL_US 50000   // 20 Hz

static volatile int g_running = 1;

static void sig_handler(int) { g_running = 0; }

// ── JSON helpers (no external dependency) ──────────────────────────────────────

static inline double extract_num(const std::string &s, const char *key,
                                 double fallback = 0.0) {
  size_t p = s.find(std::string("\"") + key + "\"");
  if (p == std::string::npos) return fallback;
  p = s.find(':', p);
  if (p == std::string::npos) return fallback;
  while (++p < s.size() && (s[p] == ' ' || s[p] == '\t')) {}
  if (p >= s.size()) return fallback;
  char *end = nullptr;
  double v = std::strtod(s.c_str() + p, &end);
  return (end == s.c_str() + p) ? fallback : v;
}

static inline std::string extract_str(const std::string &s, const char *key) {
  size_t p = s.find(std::string("\"") + key + "\"");
  if (p == std::string::npos) return "";
  p = s.find('\"', p + strlen(key) + 2);
  if (p == std::string::npos) return "";
  size_t e = s.find('\"', p + 1);
  if (e == std::string::npos) return "";
  return s.substr(p + 1, e - p - 1);
}

static std::string make_state_json(const RangerCoreState &core,
                                   const RangerActuatorState &act,
                                   const RangerCommonSensorState &sensor,
                                   bool connected) {
  std::ostringstream js;
  js << std::fixed << std::setprecision(4);
  js << "{";

  // motion
  js << "\"linear_velocity\":"  << core.motion_state.linear_velocity  << ",";
  js << "\"angular_velocity\":" << core.motion_state.angular_velocity << ",";
  js << "\"lateral_velocity\":" << core.motion_state.lateral_velocity  << ",";
  js << "\"steering_angle\":"   << core.motion_state.steering_angle    << ",";

  // system
  js << "\"battery_voltage\":"  << core.system_state.battery_voltage   << ",";
  js << "\"vehicle_state\":"    << (int)core.system_state.vehicle_state << ",";
  js << "\"control_mode\":"     << (int)core.system_state.control_mode  << ",";
  js << "\"error_code\":"       << core.system_state.error_code        << ",";
  js << "\"fault_code\":"       << core.system_state.error_code        << ",";
  js << "\"motion_mode\":"      << (int)core.motion_mode_state.motion_mode << ",";
  js << "\"mode_changing\":"    << (int)core.motion_mode_state.mode_changing << ",";

  // RC
  js << "\"rc_connected\":" << (core.rc_state.swb != RC_SWITCH_DOWN ? "true" : "false") << ",";
  js << "\"rc_swa\":" << (int)core.rc_state.swa << ",";
  js << "\"rc_swb\":" << (int)core.rc_state.swb << ",";
  js << "\"rc_swc\":" << (int)core.rc_state.swc << ",";
  js << "\"rc_swd\":" << (int)core.rc_state.swd << ",";
  js << "\"rc_stick_right_v\":"  << (int)core.rc_state.stick_right_v  << ",";
  js << "\"rc_stick_right_h\":"  << (int)core.rc_state.stick_right_h  << ",";
  js << "\"rc_stick_left_v\":"   << (int)core.rc_state.stick_left_v   << ",";
  js << "\"rc_stick_left_h\":"   << (int)core.rc_state.stick_left_h   << ",";

  // odometry
  js << "\"odom_left\":"  << core.odometry.left_wheel  << ",";
  js << "\"odom_right\":" << core.odometry.right_wheel << ",";

  // motor speeds
  js << "\"motor_speeds\":[" << act.motor_speeds.speed_1 << ","
                             << act.motor_speeds.speed_2 << ","
                             << act.motor_speeds.speed_3 << ","
                             << act.motor_speeds.speed_4 << "],";

  // motor angles
  js << "\"motor_angles\":[" << act.motor_angles.angle_5 << ","
                             << act.motor_angles.angle_6 << ","
                             << act.motor_angles.angle_7 << ","
                             << act.motor_angles.angle_8 << "],";

  // actuator HS
  js << "\"actuator_hs\":[";
  for (int i = 0; i < 8; ++i) {
    if (i) js << ",";
    js << "{\"rpm\":" << act.actuator_hs_state[i].rpm
       << ",\"current\":" << act.actuator_hs_state[i].current
       << ",\"pulse_count\":" << act.actuator_hs_state[i].pulse_count << "}";
  }
  js << "],";

  // actuator LS
  js << "\"actuator_ls\":[";
  for (int i = 0; i < 8; ++i) {
    if (i) js << ",";
    js << "{\"driver_voltage\":" << act.actuator_ls_state[i].driver_voltage
       << ",\"driver_temp\":"    << act.actuator_ls_state[i].driver_temp
       << ",\"motor_temp\":"     << act.actuator_ls_state[i].motor_temp
       << ",\"driver_state\":"   << (int)act.actuator_ls_state[i].driver_state << "}";
  }
  js << "],";

  // BMS
  js << "\"bms_soc\":"         << (int)sensor.bms_basic_state.battery_soc  << ",";
  js << "\"bms_soh\":"         << (int)sensor.bms_basic_state.battery_soh  << ",";
  js << "\"bms_voltage\":"     << sensor.bms_basic_state.voltage           << ",";
  js << "\"bms_current\":"     << sensor.bms_basic_state.current           << ",";
  js << "\"bms_temperature\":" << sensor.bms_basic_state.temperature       << ",";

  // BMS extended alarms (bitmask)
  js << "\"bms_alarm1\":" << (int)sensor.bms_extended_state.alarm_status_1 << ",";
  js << "\"bms_alarm2\":" << (int)sensor.bms_extended_state.alarm_status_2 << ",";
  js << "\"bms_warn1\":"  << (int)sensor.bms_extended_state.warn_status_1  << ",";
  js << "\"bms_warn2\":"  << (int)sensor.bms_extended_state.warn_status_2  << ",";

  // connection flag
  js << "\"connected\":" << (connected ? "true" : "false");

  js << "}";
  return js.str();
}

// ── main ───────────────────────────────────────────────────────────────────────

int main(int argc, char *argv[]) {
  if (argc < 2) {
    std::cerr << "Usage: ugv_bridge <can_iface> [port]" << std::endl;
    std::cerr << "Example: ugv_bridge can0" << std::endl;
    return 1;
  }

  std::string can_dev = argv[1];
  int port = BRIDGE_PORT;
  if (argc >= 3) port = std::stoi(argv[2]);

  signal(SIGINT, sig_handler);
  signal(SIGTERM, sig_handler);

  // ── init UGV SDK ──────────────────────────────────────────────────────────
  std::cout << "[bridge] Connecting to Ranger Mini 3.0 on " << can_dev << " ..." << std::endl;
  auto robot = std::make_shared<RangerRobot>(RangerRobot::Variant::kRangerMiniV3);

  try {
    robot->Connect(can_dev);
    robot->ResetRobotState();
    robot->EnableCommandedMode();
  } catch (const std::exception &e) {
    std::cerr << "[bridge] CAN init failed: " << e.what() << std::endl;
    return 1;
  }
  std::cout << "[bridge] CAN connected, mode=CMD" << std::endl;

  // ── init TCP server ───────────────────────────────────────────────────────
  int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
  if (listen_fd < 0) { perror("socket"); return 1; }

  int opt = 1;
  setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

  struct sockaddr_in addr = {};
  addr.sin_family      = AF_INET;
  addr.sin_addr.s_addr = INADDR_ANY;
  addr.sin_port        = htons(port);

  if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
    perror("bind");
    close(listen_fd);
    return 1;
  }
  if (listen(listen_fd, BACKLOG) < 0) {
    perror("listen");
    close(listen_fd);
    return 1;
  }
  std::cout << "[bridge] TCP server listening on :" << port << std::endl;

  // ── main loop ─────────────────────────────────────────────────────────────
  int client_fd = -1;
  char rx_buf[1024];

  while (g_running) {
    struct timeval tv = {0, 100000};  // 100 ms
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(listen_fd, &fds);
    int maxfd = listen_fd;
    if (client_fd >= 0) {
      FD_SET(client_fd, &fds);
      if (client_fd > maxfd) maxfd = client_fd;
    }

    int sel = select(maxfd + 1, &fds, nullptr, nullptr, &tv);
    if (sel < 0) {
      if (errno == EINTR) continue;
      perror("select");
      break;
    }

    // new connection
    if (FD_ISSET(listen_fd, &fds)) {
      int new_fd = accept(listen_fd, nullptr, nullptr);
      if (new_fd >= 0) {
        if (client_fd >= 0) {
          std::cout << "[bridge] Replacing existing client" << std::endl;
          close(client_fd);
        }
        client_fd = new_fd;
        std::cout << "[bridge] Client connected (fd=" << client_fd << ")" << std::endl;
      }
    }

    // client data
    if (client_fd >= 0 && FD_ISSET(client_fd, &fds)) {
      ssize_t n = recv(client_fd, rx_buf, sizeof(rx_buf) - 1, 0);
      if (n <= 0) {
        std::cout << "[bridge] Client disconnected" << std::endl;
        close(client_fd);
        client_fd = -1;
      } else {
        rx_buf[n] = '\0';
        std::string cmd_json(rx_buf);
        std::string cmd = extract_str(cmd_json, "cmd");

        if (cmd == "velocity") {
          double linear  = extract_num(cmd_json, "linear", 0.0);
          double angular = extract_num(cmd_json, "angular", 0.0);
          robot->SetMotionMode(RangerInterface::MotionMode::kDualAckerman);
          robot->SetMotionCommand(linear, angular);
        } else if (cmd == "estop") {
          robot->SetMotionCommand(0.0, 0.0);
          std::cout << "[bridge] ESTOP" << std::endl;
        } else if (cmd == "enable") {
          robot->ResetRobotState();
          robot->EnableCommandedMode();
          std::cout << "[bridge] Fault cleared, commanded mode re-enabled" << std::endl;
        } else if (cmd == "reset") {
          robot->ResetRobotState();
          std::cout << "[bridge] Robot state reset" << std::endl;
        } else {
          std::cerr << "[bridge] Unknown cmd: " << cmd << std::endl;
        }
      }
    }

    // send state (regardless of client, to keep state fresh)
    static auto last_send = std::chrono::steady_clock::now();
    auto now = std::chrono::steady_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(now - last_send).count();

    if (elapsed >= STATE_INTERVAL_US) {
      last_send = now;

      RangerCoreState          core   = robot->GetRobotState();
      RangerActuatorState      act    = robot->GetActuatorState();
      RangerCommonSensorState  sensor = robot->GetCommonSensorState();

      std::string json = make_state_json(core, act, sensor, client_fd >= 0);
      json += "\n";

      if (client_fd >= 0) {
        ssize_t sent = send(client_fd, json.c_str(), json.size(), MSG_NOSIGNAL);
        if (sent < 0) {
          std::cout << "[bridge] Send error, closing client" << std::endl;
          close(client_fd);
          client_fd = -1;
        }
      }
    }
  }

  // cleanup
  std::cout << "\n[bridge] Shutting down ..." << std::endl;
  robot->SetMotionCommand(0.0, 0.0);
  if (client_fd >= 0) close(client_fd);
  close(listen_fd);
  std::cout << "[bridge] Done." << std::endl;
  return 0;
}
