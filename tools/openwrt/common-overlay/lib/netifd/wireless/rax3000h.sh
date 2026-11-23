#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Vendor cfg80211 supports APs, but not mac80211's interface/bridge lifecycle.
. /lib/netifd/netifd-wireless.sh
init_wireless_driver "$@"

drv_rax3000h_cleanup() { :; }
drv_rax3000h_init_vlan_config() { :; }
drv_rax3000h_init_station_config() { :; }

drv_rax3000h_init_device_config() {
	config_add_string country
	config_add_int rax_auto_fallback rax_txpower
}
drv_rax3000h_init_iface_config() {
	config_add_boolean hidden isolate
	config_add_int ieee80211w sae_pwe maxassoc
}

rax_error() {
	rax_failed=1
	logger -t rax-wifi "$__netifd_device: $1"
	# Setup can fail after creating a VIF but before registering it as up.
	wireless_set_data "ifnames=$rax_ifnames"
	rax_cleanup "$rax_ifnames"
	wireless_setup_failed "$1"
}

rax_cleanup() {
	local vap pid n
	for vap in $1; do
		pid=$(cat "/var/run/rax-wifi/$vap.pid" 2>/dev/null)
		[ -z "$pid" ] || kill "$pid" 2>/dev/null
		n=0
		while [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && [ "$n" -lt 5 ]; do
			sleep 1; n=$((n + 1))
		done
		[ -z "$pid" ] || kill -KILL "$pid" 2>/dev/null
		brctl delif br-lan "$vap" 2>/dev/null
		ip link set "$vap" down 2>/dev/null
		rm -f "/var/run/rax-wifi/$vap.conf" "/var/run/rax-wifi/$vap.pid" "/var/run/hostapd/$vap"
	done
}

rax_prepare() {
	local n=0
	while ! mkdir /var/run/rax-wifi-init.lock 2>/dev/null; do
		n=$((n + 1)); [ "$n" -lt 20 ] || return 1
		sleep 1
	done
	if [ ! -e /var/run/rax-wifi-ready ]; then
		/usr/bin/rax-network wifi >/tmp/wifi-load.log 2>&1 || {
			rmdir /var/run/rax-wifi-init.lock
			return 1
		}
		touch /var/run/rax-wifi-ready
	fi
	rmdir /var/run/rax-wifi-init.lock
	n=0
	while [ ! -d /sys/class/net/br-lan ]; do
		n=$((n + 1)); [ "$n" -lt 20 ] || return 1
		sleep 1
	done
}

rax_channel_config() {
	case "$htmode" in HE*) rax_ax=1 ;; esac
	case "$band:$htmode" in 5g:VHT*|5g:HE*) rax_ac=1 ;; esac
	case "$htmode" in *40|*80|*160)
		if [ "$band" = 2g ]; then
			[ "$channel" -le 7 ] && rax_secondary=+ || rax_secondary=-
		else
			[ "$channel" != 165 ] || { rax_error INVALID_CHANNEL_WIDTH; return 1; }
			local offset=36
			[ "$channel" -lt 149 ] || offset=149
			[ "$(((channel - offset) / 4 % 2))" = 0 ] && rax_secondary=+ || rax_secondary=-
		fi ;;
	esac
	case "$htmode" in *80|*160)
		rax_width=1
		for rax_center in 42 58 106 122 138 155; do
			[ "$channel" -ge "$((rax_center - 6))" ] && [ "$channel" -le "$((rax_center + 6))" ] && break
		done
		case "$htmode" in *160)
			rax_width=2
			case "$channel" in 36|40|44|48|52|56|60|64) rax_center=50 ;; 100|104|108|112|116|120|124|128) rax_center=114 ;; *) rax_error INVALID_CHANNEL_WIDTH; return 1 ;; esac ;;
		esac ;;
	esac
}

rax_vif() {
	local name="$1" mode ssid encryption key hidden isolate ieee80211w sae_pwe maxassoc network
	local vap conf pid state n hex key_mgmt pmf
	json_select config
	json_get_vars mode ssid encryption key hidden isolate ieee80211w sae_pwe maxassoc
	json_get_values network network
	json_select ..
	[ "$rax_failed" = 0 ] || return
	[ "$mode" = ap ] || { rax_error UNSUPPORTED_MODE; return; }
	[ "$network" = lan ] || { rax_error UNSUPPORTED_NETWORK; return; }
	[ "$rax_index" -lt 8 ] || { rax_error TOO_MANY_INTERFACES; return; }
	[ -n "$ssid" ] && [ "${#ssid}" -le 32 ] || { rax_error INVALID_SSID; return; }
	case "$ssid" in *"
"*|*"$(printf '\r')"*) rax_error INVALID_SSID; return ;; esac
	[ -z "$maxassoc" ] || { [ "$maxassoc" -ge 1 ] && [ "$maxassoc" -le 128 ]; } || { rax_error INVALID_MAXASSOC; return; }
	case "$encryption" in
		none|'') key_mgmt=; pmf=0 ;;
		psk2|psk2+ccmp|psk2+aes) key_mgmt=WPA-PSK; pmf=${ieee80211w:-0} ;;
		sae) key_mgmt=SAE; pmf=${ieee80211w:-2}; [ "$pmf" = 2 ] || { rax_error INVALID_PMF; return; } ;;
		sae-mixed) key_mgmt='WPA-PSK SAE'; pmf=${ieee80211w:-1}; [ "$pmf" != 0 ] || { rax_error INVALID_PMF; return; } ;;
		*) rax_error UNSUPPORTED_ENCRYPTION; return ;;
	esac
	case "$pmf" in 0|1|2) ;; *) rax_error INVALID_PMF; return ;; esac
	case "$encryption:${sae_pwe:-0}" in sae*:0|psk2*:*|none:*|:*) ;; *) rax_error UNSUPPORTED_SAE_PWE; return ;; esac
	if [ -n "$key_mgmt" ]; then
		[ "${#key}" -ge 8 ] && [ "${#key}" -le 63 ] || { rax_error INVALID_KEY; return; }
		case "$key" in *"
"*|*"$(printf '\r')"*) rax_error INVALID_KEY; return ;; esac
	fi
	vap="vap$((rax_base + rax_index))"; rax_index=$((rax_index + 1))
	rax_ifnames="$rax_ifnames $vap"
	wireless_set_data "ifnames=$rax_ifnames"
	[ -d "/sys/class/net/$vap" ] || echo "Hisilicon0 create $vap ap $band" >/sys/hisys/hipriv
	[ -d "/sys/class/net/$vap" ] || { rax_error INTERFACE_CREATE_FAILED; return; }
	if [ -x /usr/sbin/rax-mac ]; then
		ip link set "$vap" address "$(/usr/sbin/rax-mac "$((16 + ${vap#vap}))")" || { rax_error MAC_FAILED; return; }
	fi
	if [ "$rax_auto_pending" = 1 ]; then
		local selected scan_width=20 scan_log="/var/run/rax-wifi/$vap-scan.log" line
		rax_auto_pending=0
		case "$htmode" in *40) scan_width=40 ;; *80) scan_width=80 ;; *160) scan_width=160 ;; esac
		logger -t rax-wifi "$__netifd_device: pre-AP automatic scan on $vap; fallback channel=$channel width=$scan_width"
		# Bring up the netdev for scanning, before hostapd starts the actual AP.
		ip link set "$vap" up || { rax_error INTERFACE_UP_FAILED; return; }
		if selected=$(/usr/sbin/rax-wifi-scan "$vap" "$scan_width" "$channel" 2>"$scan_log"); then
			channel=$selected
			rax_channel_config || return
		fi
		while IFS= read -r line; do logger -t rax-wifi "$__netifd_device: $line"; done <"$scan_log"
	fi
	conf="/var/run/rax-wifi/$vap.conf"; pid="/var/run/rax-wifi/$vap.pid"
	hex=$(printf '%s' "$ssid" | hexdump -ve '1/1 "%02x"')
	{
		printf 'interface=%s\ndriver=nl80211\nbridge=br-lan\nctrl_interface=/var/run/hostapd\nssid2=%s\n' "$vap" "$hex"
		printf 'country_code=%s\nieee80211d=1\nhw_mode=%s\nchannel=%s\nwmm_enabled=1\nauth_algs=1\n' "$country" "$rax_hw" "$channel"
		[ "$band" = 5g ] && echo 'ieee80211h=1'
		printf 'ignore_broadcast_ssid=%s\nap_isolate=%s\n' "${hidden:-0}" "${isolate:-0}"
		[ -z "$maxassoc" ] || printf 'max_num_sta=%s\n' "$maxassoc"
		[ "$htmode" = NOHT ] || echo 'ieee80211n=1'
		[ "$rax_secondary" = 0 ] || printf 'ht_capab=[HT40%s][SHORT-GI-20][SHORT-GI-40]\n' "$rax_secondary"
		if [ "$rax_ac" = 1 ]; then
			printf 'ieee80211ac=1\nvht_oper_chwidth=%s\n' "$rax_width"
			[ "$rax_width" != 2 ] || echo 'vht_capab=[VHT160]'
			[ "$rax_center" = 0 ] || printf 'vht_oper_centr_freq_seg0_idx=%s\n' "$rax_center"
		fi
		if [ "$rax_ax" = 1 ]; then
			printf 'ieee80211ax=1\nhe_oper_chwidth=%s\n' "$rax_width"
			[ "$rax_center" = 0 ] || printf 'he_oper_centr_freq_seg0_idx=%s\n' "$rax_center"
		fi
		if [ -n "$key_mgmt" ]; then
			printf 'wpa=2\nwpa_key_mgmt=%s\nrsn_pairwise=CCMP\nwpa_passphrase=%s\nieee80211w=%s\n' "$key_mgmt" "$key" "$pmf"
			case "$encryption" in sae*) printf 'sae_require_mfp=1\nsae_pwe=0\n' ;; esac
		fi
	} >"$conf"
	/bin/hostapd -B -P "$pid" "$conf" >"/var/run/rax-wifi/$vap.log" 2>&1 || { rax_error HOSTAPD_START_FAILED; return; }
	n=0
	while [ ! -s "$pid" ]; do
		n=$((n + 1)); [ "$n" -lt 4 ] || { rax_error HOSTAPD_NO_PID; return; }
		sleep 1
	done
	wireless_add_process "$(cat "$pid")" /bin/hostapd 1
	# DFS may legitimately remain in CAC. Report it as pending until enabled.
	n=0
	while :; do
		state=$(/bin/hostapd_cli -p /var/run/hostapd -i "$vap" status 2>/dev/null | sed -n 's/^state=//p')
		[ "$state" = ENABLED ] && break
		case "$state" in DFS|COUNTRY_UPDATE|HT_SCAN|UNINITIALIZED) ;; *) rax_error HOSTAPD_NOT_ENABLED; return ;; esac
		n=$((n + 1)); [ "$n" -lt 130 ] || { rax_error HOSTAPD_TIMEOUT; return; }
		sleep 1
	done
	iwpriv "$vap" txpower "$rax_txpower" >/dev/null || { rax_error TXPOWER_FAILED; return; }
	[ -d "/sys/class/net/br-lan/brif/$vap" ] || brctl addif br-lan "$vap" || { rax_error BRIDGE_FAILED; return; }
	ip link set "$vap" up || { rax_error INTERFACE_UP_FAILED; return; }
	[ "$rax_automatic" != 1 ] || logger -t rax-wifi "$__netifd_device: $vap AP enabled; selected channel=$channel; no runtime retuning"
	wireless_add_vif "$name" "$vap"
}

rax_setup() {
	local band channel htmode country rax_auto_fallback rax_txpower
	local rax_auto_pending=0 rax_automatic=0
	local rax_base rax_hw rax_secondary=0 rax_center=0 rax_width=0 rax_ac=0 rax_ax=0
	local rax_index=0 rax_ifnames= rax_failed=0
	json_select config
	json_get_vars band channel htmode country rax_auto_fallback rax_txpower
	json_select ..
	# This private ioctl uses 0..300, unlike standard OpenWrt txpower in dBm.
	rax_txpower=${rax_txpower:-300}
	case "$rax_txpower" in *[!0-9]*) rax_error INVALID_TXPOWER; return ;; esac
	[ "$rax_txpower" -le 300 ] || { rax_error INVALID_TXPOWER; return; }
	# Calibration and current channel table are for this CN device.
	[ "${country:-CN}" = CN ] || { rax_error UNSUPPORTED_COUNTRY; return; }; country=CN
	case "$channel" in auto|0|'')
		rax_auto_pending=1; rax_automatic=1
		channel=${rax_auto_fallback:-}
		[ -n "$channel" ] || { [ "$band" = 2g ] && channel=1 || channel=36; }
		case "$band:$channel:$htmode" in 5g:165:*40|5g:165:*80) channel=36 ;; esac
		case "$band:$channel:$htmode" in
			5g:36:*160|5g:40:*160|5g:44:*160|5g:48:*160|5g:52:*160|5g:56:*160|5g:60:*160|5g:64:*160|5g:100:*160|5g:104:*160|5g:108:*160|5g:112:*160|5g:116:*160|5g:120:*160|5g:124:*160|5g:128:*160) ;;
			5g:*:*160) channel=36 ;;
		esac
	;; esac
	case "$band:$channel" in
		2g:1|2g:2|2g:3|2g:4|2g:5|2g:6|2g:7|2g:8|2g:9|2g:10|2g:11|2g:12|2g:13) rax_base=0; rax_hw=g ;;
		5g:36|5g:40|5g:44|5g:48|5g:52|5g:56|5g:60|5g:64|5g:100|5g:104|5g:108|5g:112|5g:116|5g:120|5g:124|5g:128|5g:132|5g:136|5g:140|5g:144|5g:149|5g:153|5g:157|5g:161|5g:165) rax_base=8; rax_hw=a ;;
		*) rax_error INVALID_CHANNEL; return ;;
	esac
	case "$band:$htmode" in
		2g:HT20|2g:HT40|2g:HE20|2g:HE40|5g:HT20|5g:HT40|5g:VHT20|5g:VHT40|5g:VHT80|5g:VHT160|5g:HE20|5g:HE40|5g:HE80|5g:HE160|*:NOHT) ;;
		*) rax_error INVALID_HTMODE; return ;;
	esac
	rax_channel_config || return
	umask 077
	mkdir -p /var/run/rax-wifi /var/run/hostapd
	rax_prepare || { rax_error DRIVER_INIT_FAILED; return; }
	# The vendor driver needs settling after a previous hostapd exits.
	sleep 2
	for_each_interface '' rax_vif
	wireless_set_data "ifnames=$rax_ifnames"
	[ "$rax_failed" = 0 ] || return
	wireless_set_up
}

drv_rax3000h_setup() {
	# ImmortalWrt 25 netifd serializes setup/teardown across both radios.
	rax_setup
}

drv_rax3000h_teardown() {
	local ifnames
	json_select data
	json_get_var ifnames ifnames
	json_select ..
	rax_cleanup "$ifnames"
}

add_driver rax3000h
