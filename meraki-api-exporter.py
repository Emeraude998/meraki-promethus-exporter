import http.server
import threading
import time

import configargparse
import meraki


def get_devices(devices, dashboard, organization_id):
    """
    Fetch all devices in the organization.
    
    Args:
        devices: List to append device data to
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch devices for
    
    Returns:
        List of devices in the organization
    """
    devices.extend(dashboard.organizations.getOrganizationDevicesAvailabilities(
        organizationId=organization_id,
        total_pages="all"))
    print('Got', len(devices), 'Devices')

def get_device_statuses(devices_statuses, dashboard, organization_id):
    """
    Fetch all device statuses in the organization.
    
    Args:
        devices_statuses: List to append device status data to
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch device statuses for
    
    Returns:
        List of device statuses in the organization
    """
    devices_statuses.extend(dashboard.organizations.getOrganizationDevicesUplinksLossAndLatency(
        organizationId=organization_id,
        ip='8.8.8.8',
        timespan="120",
        total_pages="all"))
    print('Got ', len(devices_statuses), 'Device Statuses')

def get_uplink_statuses(uplink_statuses, dashboard, organization_id):
    """
    Fetch all uplink statuses in the organization.
    
    Args:
        uplink_statuses: List to append uplink status data to
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch uplink statuses for
    
    Returns:
        List of uplink statuses in the organization
    """
    uplink_statuses.extend(dashboard.appliance.getOrganizationApplianceUplinkStatuses(
        organizationId=organization_id,
        total_pages="all"))
    print('Got ', len(uplink_statuses), 'Uplink Statuses')

def is_uplink_port(port_id, serial=None, port_tags_map=None):
    """
    Identify if a port is an uplink port based on tags only.
    
    Args:
        port_id: The port ID/number
        serial: Device serial number (required for tag lookup)
        port_tags_map: Dict mapping {serial: {portId: [tags]}} (required)
    
    Returns:
        bool: True if port has 'uplink' tag
    """
    # Check if port is tagged with 'uplink'
    if port_tags_map and serial and serial in port_tags_map:
        port_tags = port_tags_map[serial].get(str(port_id), [])
        if 'uplink' in port_tags:
            return True

    return False

def is_ap_device(port_id, serial=None, port_discovery_map=None):
    """
    Identify if a port is an access point port based on topology discovery.
    
    Args:
        port_id: The port ID/number
        serial: Device serial number of the switch (required)
        port_discovery_map: Dict mapping {serial: {portId: lldp_info}} (required)
    
    Returns:
        bool: True if device is an access point
        str: The device name if it is an access point, else None
    """
    if port_discovery_map and serial and (serial in port_discovery_map):
        port_info = port_discovery_map[serial].get(str(port_id), {})
        if port_info.get('device_type') == 'MR':
            return True, port_info.get('device_name', 'N/A')

    return False, None

def get_vpn_statuses(vpn_statuses, dashboard, organization_id):
    """
    Fetch all VPN statuses in the organization.
    
    Args:
        vpn_statuses: List to append VPN status data to
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch VPN statuses for
    
    Returns:
        List of VPN statuses in the organization
    """
    vpn_statuses.extend(dashboard.appliance.getOrganizationApplianceVpnStatuses(
        organizationId=organization_id,
        total_pages="all"))
    print('Got ', len(vpn_statuses), 'VPN Statuses')

def get_organization(org_data, dashboard, organization_id):
    """
    Fetch organization details.
    
    Args:
        org_data: Dict to update with organization data
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch details for
    
    Returns:
        Dict with organization details
    """
    org_data.update(dashboard.organizations.getOrganization(organizationId=organization_id))

def get_organizations(orgs_list, dashboard):
    """
    Fetch all organizations accessible by the API key.
    
    Args:
        orgs_list: List to append organization IDs to
        dashboard: Meraki DashboardAPI instance
    
    Returns:
        List of organization IDs accessible by the API key
    """
    response = dashboard.organizations.getOrganizations()
    for org in response:  # If you know better way to check that API key has access to an Org, please let me know. (This will rate throtled big time )
        try:
            dashboard.organizations.getOrganizationSummaryTopDevicesByUsage(organizationId=org['id'])
            orgs_list.append(org['id'])
        except meraki.exceptions.APIError:
            pass

def get_switch_ports_usage(switch_ports_usage, dashboard, organization_id):
    """
    Fetch switch port usage history for the organization.
    For Prometheus scraping, we need recent data
    Note: Meraki's interval data may not be available for very short timespans
    Using 2 hours as a balance between freshness and data availability

    Args:
        switch_ports_usage: List to append switch port usage data to
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch switch port usage for

    Returns:
        List of devices with switch port usage data
    """
    timespan = 7200  # 2 hours in seconds

    print(f"Fetching switch port usage history for org {organization_id}...")
    print(f"   Timespan: {timespan} seconds ({timespan/3600} hours)")

    try:
        response = dashboard.switch.getOrganizationSwitchPortsUsageHistoryByDeviceByInterval(
            organizationId=organization_id,
            timespan=timespan,
            total_pages="all"
        )

        if isinstance(response, dict) and 'items' in response:
            # Process all devices - we'll filter ports later
            all_devices = response['items']
            print(f"   Raw response: {len(all_devices)} devices returned")

            for device in all_devices:
                # Check if device has any port data
                ports = device.get('ports', [])
                if ports:
                    # Check if any port has interval data
                    has_data = any(port.get('intervals') for port in ports)
                    if has_data:
                        switch_ports_usage.append(device)

            print('Got', len(switch_ports_usage), 'devices with port data')
            print('Meta info:', response.get('meta'))
        else:
            switch_ports_usage.extend(response)
            print('Got', len(response), 'records')
    except Exception as e:
        print(f"Error fetching switch port usage: {e}")
        raise

def get_switch_ports_tags_map(port_tags_map, dashboard, organization_id):
    """
    Fetch port configuration (including tags) for all switches in the organization.
    
    Args:
        port_tags_map: Dict to update with port tags data
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch port tags for
        
    Returns:
        dict: {serial: {portId: [tags]}}
    """
    # Get all switch ports for the organization in one API call
    response = dashboard.switch.getOrganizationSwitchPortsBySwitch(
        organizationId=organization_id,
        total_pages="all"
    )

    # Response is a dict with 'items' and 'meta' when using total_pages="all"
    if isinstance(response, dict) and 'items' in response:
        switches_data = response['items']
    else:
        switches_data = response

    # Build the port tags map
    for switch in switches_data:
        serial = switch.get('serial')
        if not serial:
            continue

        port_tags_map[serial] = {}
        ports = switch.get('ports', [])

        for port in ports:
            port_id = str(port.get('portId', ''))
            tags = port.get('tags', [])
            if tags:
                port_tags_map[serial][port_id] = tags

def get_switch_ports_topology_discovery(port_discovery_map, dashboard, organization_id):
    """
    List most recently seen LLDP/CDP discovery and topology information per switch port in an organization.
    
    Args:
        port_discovery_map: Dict to update with topology discovery data
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to fetch topology discovery for
    
    Returns:
        List of topology discovery data per switch port
    """
    response = dashboard.switch.getOrganizationSwitchPortsTopologyDiscoveryByDevice(
        organizationId=organization_id,
        total_pages="all"
    )
    
    # Response is a dict with 'items' and 'meta' when using total_pages="all"
    print('Got', response['meta']['counts']['items']['total'], 'topology discovery records')
    
    if isinstance(response, dict) and 'items' in response:
        topology_data = response['items']
    else:
        topology_data = response
    
    # Build map of topology data
    for switch in topology_data:
        serial = switch.get('serial')
        if not serial:
            continue
        
        port_discovery_map[serial] = {}
        ports = switch.get('ports', [])
        
        for port in ports:
            port_id = str(port.get('portId', ''))
            cdp = port.get('cdp', [])
            lldp = port.get('lldp', [])
            
            # Check if is meraki device based on CDP/LLDP info
            if cdp or lldp:
                is_meraki, device_type, device_info = is_meraki_device(cdp, lldp)
                if is_meraki:
                    # Parse LLDP to get discovered device name
                    lldp_parsed = parse_discovery_info(lldp)
                    
                    port_discovery_map[serial][port_id] = {
                        'device_type': device_type,
                        'device_name': extract_device_name(lldp_parsed.get('system_name', 'N/A')),
                    }

def parse_discovery_info(info_list):
    """
    Parse CDP or LLDP information from list of {'name': ..., 'value': ...} dicts
    
    Args:
        info_list: List of dicts with 'name' and 'value' keys
        
    Returns:
        Dictionary with lowercased keys
    """
    result = {}
    if info_list and isinstance(info_list, list):
        for item in info_list:
            if isinstance(item, dict):
                name = item.get('name', '').lower().replace(' ', '_')
                value = item.get('value', '')
                result[name] = value
    return result

def is_meraki_device(cdp_list, lldp_list):
    """
    Determine if a device is a Meraki device based on CDP or LLDP information.
    
    Args:
        cdp_list: List of CDP information dicts
        lldp_list: List of LLDP information dicts

    Returns:
        is_meraki: bool indicating if device is Meraki
        device_type: str indicating device type
        device_info_dict: dict with parsed device info
    """
    meraki_prefixes = ['MR', 'MS', 'MX', 'MV', 'MG', 'MC', 'MV2', 'MT']
    
    # Parse CDP information
    cdp_info = parse_discovery_info(cdp_list)
    if cdp_info:
        platform = cdp_info.get('platform', '')
        
        # Check platform for Meraki device types
        for prefix in meraki_prefixes:
            if prefix in platform.upper():
                return (True, prefix, cdp_info)
        
        # Some devices might have Meraki in the platform description
        if 'MERAKI' in platform.upper():
            for prefix in meraki_prefixes:
                if prefix in platform.upper():
                    return (True, prefix, cdp_info)
    
    # Parse LLDP information
    lldp_info = parse_discovery_info(lldp_list)
    if lldp_info:
        system_name = lldp_info.get('system_name', '')
        system_description = lldp_info.get('system_description', '')
        
        # Check system name for Meraki device types
        combined_text = f"{system_name} {system_description}".upper()
        if 'MERAKI' in combined_text:
            for prefix in meraki_prefixes:
                if prefix in combined_text:
                    return (True, prefix, lldp_info)
    
    return (False, None, None)

def extract_device_name(system_name):
    """
    Extract a friendly device name from system name string.
    
    Args:
        system_name: The system name string from LLDP info

    Returns:
        A friendly device name extracted from the system name.
    """
    # Simple extraction logic: take the part before the first space
    if not system_name or system_name == 'N/A':
        return 'N/A'

    # Split by ' - ' and take the last part
    return system_name.split(' - ')[-1]

def get_usage(dashboard, organization_id):
    """
    Collect and combine various Meraki device data for the organization.

    Args:
        dashboard: Meraki DashboardAPI instance
        organization_id: ID of the organization to collect data for
        
    Returns:
        Dictionary containing combined Meraki device data
    """
    # Shared data containers for threaded collection
    devices = []
    devices_statuses = []
    uplink_statuses = []
    vpn_statuses = []
    org_data = {}
    switch_ports_usage = []
    port_tags_map = {}
    port_discovery_map = {}

    # Define all data collection tasks
    threads = [
        threading.Thread(target=get_devices, args=(devices, dashboard, organization_id)),
        threading.Thread(target=get_device_statuses, args=(devices_statuses, dashboard, organization_id)),
        threading.Thread(target=get_uplink_statuses, args=(uplink_statuses, dashboard, organization_id)),
        threading.Thread(target=get_organization, args=(org_data, dashboard, organization_id)),
        threading.Thread(target=get_switch_ports_usage, args=(switch_ports_usage, dashboard, organization_id)),
        threading.Thread(target=get_switch_ports_tags_map, args=(port_tags_map, dashboard, organization_id)),
        threading.Thread(target=get_switch_ports_topology_discovery, args=(port_discovery_map, dashboard, organization_id)),
    ]

    # Add VPN collection thread if enabled
    if 'vpn' in COLLECT_EXTRA:
        threads.append(threading.Thread(target=get_vpn_statuses, args=(vpn_statuses, dashboard, organization_id)))

    # Start all threads
    for thread in threads:
        thread.start()

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    # Fetch networks for this organization so we can report network names instead of IDs
    try:
        networks = dashboard.organizations.getOrganizationNetworks(
            organizationId=organization_id,
            total_pages="all")
        networks_map = {n.get('id'): n.get('name') for n in networks}
    except Exception:
        networks_map = {}

    print('Combining collected data\n')

    the_list = {}
    # Normalize device fields coming from different Meraki endpoints
    for device in devices:
        serial = device.get('serial')
        if not serial:
            # Skip devices without serial (some API responses may include non-serial entries)
            continue

        the_list[serial] = {}
        the_list[serial]['orgName'] = org_data.get('name', '')

        # Name: prefer explicit name, fall back to MAC when name empty
        name = device.get('name') or device.get('displayName') or device.get('mac') or serial
        if name:
            the_list[serial]['name'] = name

        # Model/product type: different endpoints may use 'model' or 'productType'
        model = device.get('model') or device.get('productType') or device.get('product_type')
        if model:
            the_list[serial]['model'] = model

        # MAC address
        mac = device.get('mac')
        if mac:
            the_list[serial]['mac'] = mac

        # Network: prefer to report the network name when possible
        network_id = None
        if isinstance(device.get('network'), dict):
            network_id = device['network'].get('id')
        network_id = network_id or device.get('networkId') or device.get('network_id')
        if network_id:
            network_name = networks_map.get(network_id) if networks_map else None
            the_list[serial]['networkName'] = network_name if network_name is not None else network_id

        # Status
        status = device.get('status')
        if status:
            the_list[serial]['status'] = status

        # IP-related fields: only set if present in the device object
        for ip_key in ('wan1Ip', 'wan2Ip', 'lanIp', 'publicIp'):
            if ip_key in device and device.get(ip_key) not in (None, ''):
                the_list[serial][ip_key] = device.get(ip_key)

        # usingCellularFailover may be present on some device types/endpoints
        if 'usingCellularFailover' in device:
            the_list[serial]['usingCellularFailover'] = device.get('usingCellularFailover')

    for device in devices_statuses:
        try:
            the_list[device['serial']]  # should give me KeyError if devices was not picked up by previous search.
        except KeyError:
            the_list[device['serial']] = {"missing data": True}

        the_list[device['serial']]['latencyMs'] = device['timeSeries'][-1]['latencyMs']
        the_list[device['serial']]['lossPercent'] = device['timeSeries'][-1]['lossPercent']

    for device in uplink_statuses:
        try:
            the_list[device['serial']]  # should give me KeyError if devices was not picked up by previous search.
        except KeyError:
            the_list[device['serial']] = {"missing data": True}
        the_list[device['serial']]['uplinks'] = {}
        for uplink in device['uplinks']:
            the_list[device['serial']]['uplinks'][uplink['interface']] = uplink['status']

    if 'vpn' in COLLECT_EXTRA:
        for vpn in vpn_statuses:
            try:
                the_list[vpn['deviceSerial']]
            except KeyError:
                the_list[vpn['deviceSerial']] = {"missing data": True}

            the_list[vpn['deviceSerial']]['vpnMode'] = vpn['vpnMode']
            the_list[vpn['deviceSerial']]['exportedSubnets'] = [subnet['subnet'] for subnet in vpn['exportedSubnets']]
            the_list[vpn['deviceSerial']]['merakiVpnPeers'] = vpn['merakiVpnPeers']
            the_list[vpn['deviceSerial']]['thirdPartyVpnPeers'] = vpn['thirdPartyVpnPeers']

    for device in switch_ports_usage:
        try:
            the_list[device['serial']]  # should give me KeyError if devices was not picked up by previous search.
        except KeyError:
            the_list[device['serial']] = {"missing data": True}
        for port in device.get('ports', []):
            port_id = str(port.get('portId', ''))
            # Only consider ports that have interval data
            if not port.get('intervals'):
                continue

            # Check if this port is an uplink port based on tags
            if not is_uplink_port(port_id, serial=device['serial'], port_tags_map=port_tags_map) and not is_ap_device(port_id, serial=device['serial'], port_discovery_map=port_discovery_map):
                continue  # Keep only uplink ports or AP ports

            latest_interval = port['intervals'][-1]  # Get the most recent interval data

            # Initialize usage and bandwidth dicts if not already present
            if 'switchPortUsage' not in the_list[device['serial']]:
                the_list[device['serial']]['switchPortUsage'] = {}
            if port_id not in the_list[device['serial']]['switchPortUsage']:
                the_list[device['serial']]['switchPortUsage'][port_id] = {}

            # Total bytes
            data_usage = latest_interval.get('data', {}).get('usage', {})
            the_list[device['serial']]['switchPortUsage'][port_id]['UsageTotalBytes'] = data_usage.get('total', 0)
            the_list[device['serial']]['switchPortUsage'][port_id]['UsageUpstreamBytes'] = data_usage.get('upstream', 0)
            the_list[device['serial']]['switchPortUsage'][port_id]['UsageDownstreamBytes'] = data_usage.get('downstream', 0)

            # Bandwidth in kbps
            bandwidth = latest_interval.get('bandwidth', {}).get('usage', {})
            the_list[device['serial']]['switchPortUsage'][port_id]['bandwidthTotalKbps'] = bandwidth.get('total', 0)
            the_list[device['serial']]['switchPortUsage'][port_id]['bandwidthUpstreamKbps'] = bandwidth.get('upstream', 0)
            the_list[device['serial']]['switchPortUsage'][port_id]['bandwidthDownstreamKbps'] = bandwidth.get('downstream', 0)

            ap, name = is_ap_device(port_id, serial=device['serial'], port_discovery_map=port_discovery_map)
            if ap:
                the_list[device['serial']]['switchPortUsage'][port_id]['ap_device_name'] = name

    print('Done')
    return the_list
# end of get_usage()


class MyHandler(http.server.BaseHTTPRequestHandler):
    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()

    def _set_headers_404(self):
        self.send_response(404)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        # Root HTML page listing organizations with links
        if self.path == "/":
            dashboard = meraki.DashboardAPI(API_KEY, output_log=False, print_console=False, maximum_retries=20, caller="promethusExporter Emeraude998")
            try:
                orgs = dashboard.organizations.getOrganizations()
            except meraki.exceptions.APIError:
                orgs = []

            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()

            html = [
                "<!DOCTYPE html>",
                "<html>",
                "<head>",
                "  <meta charset='utf-8'>",
                "  <title>Meraki Organizations</title>",
                "  <style>",
                "    body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f6f8fa;color:#24292f;}",
                "    header{background:#24292f;color:#fff;padding:16px 24px;}",
                "    header h1{margin:0;font-size:20px;}",
                "    header p{margin:4px 0 0;font-size:13px;color:#d1d5da;}",
                "    main{padding:20px 24px;}",
                "    footer{margin-top:32px;padding:12px 24px;font-size:12px;color:#586069;border-top:1px solid #e1e4e8;background:#fafbfc;}",
                "    table{border-collapse:collapse;width:100%;max-width:960px;background:#fff;border:1px solid #e1e4e8;box-shadow:0 1px 2px rgba(0,0,0,0.03);}",
                "    th,td{border-bottom:1px solid #e1e4e8;padding:8px 10px;font-size:13px;text-align:left;}",
                "    th{background:#f6f8fa;font-weight:600;}",
                "    tr:nth-child(even){background:#fafbfc;}",
                "    a{color:#0366d6;text-decoration:none;}",
                "    a:hover{text-decoration:underline;}",
                "  </style>",
                "</head>",
                "<body>",
                "  <header>",
                "    <h1>Meraki Organizations</h1>",
                "    <p>Select an organization below to view Prometheus metrics for its devices.</p>",
                "  </header>",
                "  <main>",
                "    <table>",
                "      <tr><th>Name</th><th>ID</th><th>Link</th></tr>",
            ]

            for org in orgs:
                org_id = org.get('id', '')
                org_name = org.get('name', org_id)
                link = f"/?target={org_id}"
                html.append(f"      <tr><td>{org_name}</td><td>{org_id}</td><td><a href='{link}'>{link}</a></td></tr>")

            html.extend([
                "    </table>",
                "  </main>",
                "  <footer>",
                "    Meraki Dashboard Prometheus Exporter &mdash; scrape metrics from <code>/?target=&lt;org_id&gt;</code>.",
                "  </footer>",
                "</body>",
                "</html>",
            ])

            self.wfile.write("\n".join(html).encode('utf-8'))
            return()

        if "/?target=" not in self.path and "/organizations" not in self.path and self.path != "/":
            self._set_headers_404()
            return()

        self._set_headers()
        dashboard = meraki.DashboardAPI(API_KEY, output_log=False, print_console=True, maximum_retries=20, caller="promethusExporter Emeraude998")

        if "/organizations" in self.path:   # Generating list of avialable organizations for API keys.
            org_list = list()
            get_organizations(org_list, dashboard)
            response = "- targets:\n   - " + "\n   - ".join(org_list)
            self.wfile.write(response.encode('utf-8'))
            self.wfile.write("\n".encode('utf-8'))
            return

        dest_orgId = self.path.split('=')[1]
        print('Target: ', dest_orgId)
        organization_id = str(dest_orgId)

        start_time = time.monotonic()

        host_stats = get_usage(dashboard, organization_id)
        print("Reporting on:", len(host_stats), "hosts")

        uplink_statuses = {'active': 0, 'ready': 1, 'connecting': 2, 'not connected': 3, 'failed': 4}

        response = """
# HELP meraki_device_latency The latency of the Meraki device in milliseconds
# TYPE meraki_device_latency gauge
# UNIT meraki_device_latency milliseconds
# HELP meraki_device_loss_percent The packet loss percentage of the Meraki device
# TYPE meraki_device_loss_percent gauge
# UNIT meraki_device_loss_percent percent
# HELP meraki_device_status The status of the Meraki device (1 for online, 0 for offline)
# TYPE meraki_device_status gauge
# UNIT meraki_device_status boolean
# HELP meraki_device_uplink_status The status of the uplink of the Meraki device
# TYPE meraki_device_uplink_status gauge
# UNIT meraki_device_uplink_status status_code
# HELP meraki_device_using_cellular_failover Whether the Meraki device is using cellular failover (1 for true, 0 for false)
# TYPE meraki_device_using_cellular_failover gauge
# UNIT meraki_device_using_cellular_failover boolean
# HELP meraki_switch_port_usage_total_bytes Total data usage on switch port in bytes
# TYPE meraki_switch_port_usage_total_bytes gauge
# HELP meraki_switch_port_usage_upstream_bytes Upstream data usage on switch port in bytes
# TYPE meraki_switch_port_usage_upstream_bytes gauge
# HELP meraki_switch_port_usage_downstream_bytes Downstream data usage on switch port in bytes
# TYPE meraki_switch_port_usage_downstream_bytes gauge
# HELP meraki_switch_port_bandwidth_total_kbps Total bandwidth usage on switch port in kbps
# TYPE meraki_switch_port_bandwidth_total_kbps gauge
# HELP meraki_switch_port_bandwidth_upstream_kbps Upstream bandwidth usage on switch port in kbps
# TYPE meraki_switch_port_bandwidth_upstream_kbps gauge
# HELP meraki_switch_port_bandwidth_downstream_kbps Downstream bandwidth usage on switch port in kbps
# TYPE meraki_switch_port_bandwidth_downstream_kbps gauge
# HELP meraki_wireless_usage_total_bytes Total wireless usage in bytes
# TYPE meraki_wireless_usage_total_bytes gauge
# HELP meraki_wireless_usage_sent_bytes Wireless sent usage in bytes
# TYPE meraki_wireless_usage_sent_bytes gauge
# HELP meraki_wireless_usage_received_bytes Wireless received usage in bytes
# TYPE meraki_wireless_usage_received_bytes gauge
# HELP meraki_wireless_bandwidth_total_kbps Total wireless bandwidth in kbps
# TYPE meraki_wireless_bandwidth_total_kbps gauge
# HELP meraki_wireless_bandwidth_sent_kbps Wireless sent bandwidth in kbps
# TYPE meraki_wireless_bandwidth_sent_kbps gauge
# HELP meraki_wireless_bandwidth_received_kbps Wireless received bandwidth in kbps
# TYPE meraki_wireless_bandwidth_received_kbps gauge
"""
        if 'vpn' in COLLECT_EXTRA:
            response +="""
# HELP meraki_vpn_mode The VPN mode of the Meraki device (1 for hub, 0 for spoke)
# TYPE meraki_vpn_mode gauge
# UNIT meraki_vpn_mode boolean
# HELP meraki_vpn_exported_subnets The exported subnets of the Meraki VPN
# TYPE meraki_vpn_exported_subnets gauge
# UNIT meraki_vpn_exported_subnets count
# HELP meraki_vpn_meraki_peers The Meraki VPN peers of the Meraki VPN
# TYPE meraki_vpn_meraki_peers gauge
# UNIT meraki_vpn_meraki_peers count
# HELP meraki_vpn_third_party_peers The third-party VPN peers of the Meraki VPN
# TYPE meraki_vpn_third_party_peers gauge
# UNIT meraki_vpn_third_party_peers count
"""
        # helper to escape label values for Prometheus exposition format
        def _esc(val):
            if val is None:
                return 'None'
            s = str(val)
            s = s.replace('\\', '\\\\')
            s = s.replace('"', '\\"')
            return s

        for host in host_stats.keys():
            # The getOrganizationDevicesUplinksLossAndLatency can return devices with no serial numbers.
            if host is None:
                continue

            hs = host_stats.get(host, {}) if isinstance(host_stats, dict) else {}

            name_label = hs.get('name') or hs.get('mac') or host
            network_name_label = hs.get('networkName') if isinstance(hs.get('networkName'), str) else (hs.get('networkId') if hs.get('networkId') else 'None')
            orgname_label = hs.get('orgName', 'None')

            target = '{name="' + _esc(name_label) + '",office="' + _esc(network_name_label) + '"'
            try:
                if host_stats[host]['latencyMs'] is not None:
                    response += 'meraki_device_latency' + target + '} ' + str(host_stats[host]['latencyMs']/1000) + '\n'
                if host_stats[host]['lossPercent'] is not None:
                    response += 'meraki_device_loss_percent' + target + '} ' + str(host_stats[host]['lossPercent']) + '\n'
            except KeyError:
                pass
            try:
                response += 'meraki_device_status' + target + '} ' + ('1' if host_stats[host]['status'] == 'online' else '0') + '\n'
            except KeyError:
                pass
            try:
                response += 'meraki_device_using_cellular_failover' + target + '} ' + ('1' if host_stats[host]['usingCellularFailover'] else '0') + '\n'
            except KeyError:
                pass
            if 'uplinks' in host_stats[host]:
                for uplink in host_stats[host]['uplinks'].keys():
                    response += 'meraki_device_uplink_status' + target + ',uplink="' + uplink + '"} ' + str(uplink_statuses[host_stats[host]['uplinks'][uplink]]) + '\n'
            if 'vpnMode' in host_stats[host]:
                response += 'meraki_vpn_mode' + target + '} ' + ('1' if host_stats[host]['vpnMode'] == 'hub' else '0') + '\n'
            if 'exportedSubnets' in host_stats[host]:
                for subnet in host_stats[host]['exportedSubnets']:
                    response += 'meraki_vpn_exported_subnets' + target + ',subnet="' + subnet + '"} 1\n'
            if 'merakiVpnPeers' in host_stats[host]:
                for peer in host_stats[host]['merakiVpnPeers']:
                    reachability_value = '1' if peer['reachability'] == 'reachable' else '0'
                    response += 'meraki_vpn_meraki_peers' + target + ',peer_networkId="' + peer['networkId'] + '",peer_networkName="' + peer['networkName'] + '",reachability="' + peer['reachability'] + '"} ' + reachability_value + '\n'
            if 'thirdPartyVpnPeers' in host_stats[host]:
                for peer in host_stats[host]['thirdPartyVpnPeers']:
                    reachability_value = '1' if peer['reachability'] == 'reachable' else '0'
                    response += 'meraki_vpn_third_party_peers' + target + ',peer_name="' + peer['name'] + '",peer_publicIp="' + peer['publicIp'] + '",reachability="' + peer['reachability'] + '"} ' + reachability_value + '\n'
            if 'switchPortUsage' in host_stats[host]:
                for port_id, usage_data in host_stats[host]['switchPortUsage'].items():
                    if 'ap_device_name' in usage_data:
                        ap_target = '{name="' + _esc(usage_data['ap_device_name']) + '",office="' + _esc(network_name_label) + '"}'
                        if 'UsageTotalBytes' in usage_data:
                            response += 'meraki_wireless_usage_total_bytes' + ap_target + '} ' + str(usage_data['UsageTotalBytes']*1024) + '\n'
                        if 'UsageUpstreamBytes' in usage_data:
                            response += 'meraki_wireless_usage_received_bytes' + ap_target + '} ' + str(usage_data['UsageUpstreamBytes']*1024) + '\n'
                        if 'UsageDownstreamBytes' in usage_data:
                            response += 'meraki_wireless_usage_sent_bytes' + ap_target + '} ' + str(usage_data['UsageDownstreamBytes']*1024) + '\n'
                        if 'bandwidthTotalKbps' in usage_data:
                            response += 'meraki_wireless_bandwidth_total_kbps' + ap_target + '} ' + str(usage_data['bandwidthTotalKbps']) + '\n'
                        if 'bandwidthUpstreamKbps' in usage_data:
                            response += 'meraki_wireless_bandwidth_received_kbps' + ap_target + '} ' + str(usage_data['bandwidthUpstreamKbps']) + '\n'
                        if 'bandwidthDownstreamKbps' in usage_data:
                            response += 'meraki_wireless_bandwidth_sent_kbps' + ap_target + '} ' + str(usage_data['bandwidthDownstreamKbps']) + '\n'
                        continue  # Skip to next port after reporting AP wireless usage
                    
                    if 'UsageTotalBytes' in usage_data:
                        response += 'meraki_switch_port_usage_total_bytes' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['UsageTotalBytes']*1024) + '\n'
                    if 'UsageUpstreamBytes' in usage_data:
                        response += 'meraki_switch_port_usage_upstream_bytes' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['UsageUpstreamBytes']*1024) + '\n'
                    if 'UsageDownstreamBytes' in usage_data:
                        response += 'meraki_switch_port_usage_downstream_bytes' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['UsageDownstreamBytes']*1024) + '\n'
                    if 'bandwidthTotalKbps' in usage_data:
                        response += 'meraki_switch_port_bandwidth_total_kbps' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['bandwidthTotalKbps']) + '\n'
                    if 'bandwidthUpstreamKbps' in usage_data:
                        response += 'meraki_switch_port_bandwidth_upstream_kbps' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['bandwidthUpstreamKbps']) + '\n'
                    if 'bandwidthDownstreamKbps' in usage_data:
                        response += 'meraki_switch_port_bandwidth_downstream_kbps' + target + ',portId="' + _esc(port_id) + '"} ' + str(usage_data['bandwidthDownstreamKbps']) + '\n'      

        response += '# TYPE request_processing_seconds summary\n'
        response += 'request_processing_seconds ' + str(time.monotonic() - start_time) + '\n'

        self.wfile.write(response.encode('utf-8'))

    def do_HEAD(self):
        self._set_headers()

    def do_POST(self):
        # Doesn't do anything with posted data
        self._set_headers_404()
        return()
        self._set_headers()


if __name__ == '__main__':
    parser = configargparse.ArgumentParser(description='Per-User traffic stats Pronethetius exporter for Meraki API.')
    parser.add_argument('-k', metavar='API_KEY', type=str, required=True,
                        env_var='MERAKI_API_KEY', help='API Key')
    parser.add_argument('-p', metavar='http_port', type=int, default=9822,
                        help='HTTP port to listen for Prometheus scraper, default 9822')
    parser.add_argument('-i', metavar='bind_to_ip', type=str, default="",
                        help='IP address where HTTP server will listen, default all interfaces')
    parser.add_argument('--vpn', dest='collect_vpn_data', action='store_true',
                        help='If set VPN connection statuses will be also collected')
    args = vars(parser.parse_args())
    HTTP_PORT_NUMBER = args['p']
    HTTP_BIND_IP = args['i']
    API_KEY = args['k']
    COLLECT_EXTRA = ()
    COLLECT_EXTRA += ( ('vpn',) if args['collect_vpn_data'] else () )


    # starting server
    server_class = MyHandler
    httpd = http.server.ThreadingHTTPServer((HTTP_BIND_IP, HTTP_PORT_NUMBER), server_class)
    print(time.asctime(), "Server Starts - %s:%s" % ("*" if HTTP_BIND_IP == '' else HTTP_BIND_IP, HTTP_PORT_NUMBER))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    print(time.asctime(), "Server Stops - %s:%s" % ("localhost", HTTP_PORT_NUMBER))
