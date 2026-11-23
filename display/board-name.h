// Lets the board's name on the network (its hostname) be changed from the settings page.
// ESPHome fixes the name when the firmware is built, so this puts the chosen name in
// place of it: for the router (DHCP) and for <name>.local (mDNS).
#pragma once
#include <string>
#include "esp_netif.h"
#ifdef USE_MDNS
#include <mdns.h>
#endif

namespace board_name {

// Hostnames may only hold letters, digits and hyphens: "P1S 4 display" -> "p1s-4-display".
inline std::string tidy(const std::string &in) {
  std::string out;
  for (char c : in) {
    if (c >= 'A' && c <= 'Z') c = c - 'A' + 'a';
    bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
    if (ok) out += c;
    else if (!out.empty() && out.back() != '-') out += '-';
    if (out.size() >= 31) break;
  }
  while (!out.empty() && out.back() == '-') out.pop_back();
  return out;
}

// Called every second. Cheap when nothing needs doing.
inline void apply(const std::string &wanted, bool connected) {
  static std::string announced;
  if (wanted.empty()) return;
  esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
  if (sta != nullptr) {
    const char *now = nullptr;
    if (esp_netif_get_hostname(sta, &now) != ESP_OK || now == nullptr || wanted != now)
      esp_netif_set_hostname(sta, wanted.c_str());
  }
#ifdef USE_MDNS
  if (connected && announced != wanted && mdns_hostname_set(wanted.c_str()) == ESP_OK) {
    mdns_instance_name_set(wanted.c_str());
    announced = wanted;
  }
#endif
}

}  // namespace board_name
