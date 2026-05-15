"""
Standardized REST API (v1) using flask-smorest and marshmallow.
"""
from flask import Response
from flask.views import MethodView
from flask_smorest import Blueprint, abort
from marshmallow import Schema, fields
from db import r
from ipam import (
    all_networks, find_network_by_cidr,
    find_next_free_ip, claim_ip_atomic, get_ip,
)

api_v1_bp = Blueprint("api_v1", "api_v1", url_prefix="/api/v1", description="Standardized IPAM API")

# ── Schemas ────────────────────────────────────────────────────────────────────

class SubnetSchema(Schema):
    """Schema for Subnet data."""
    id = fields.Str(dump_only=True)
    name = fields.Str()
    cidr = fields.Str()
    vlan = fields.Str()
    description = fields.Str()
    project_id = fields.Str()

class IPRecordSchema(Schema):
    """Schema for IP record details."""
    ip = fields.Str(required=True)
    hostname = fields.Str()
    description = fields.Str()
    status = fields.Str()
    network_id = fields.Str()

class IPRequestSchema(Schema):
    """Schema for requesting a new IP."""
    hostname = fields.Str(load_default="")
    description = fields.Str(load_default="API requested")
    ttl = fields.Int(load_default=None) # TTL in seconds

# ── Routes ─────────────────────────────────────────────────────────────────────

@api_v1_bp.route("/subnets")
class Subnets(MethodView):
    """Operations on subnets."""
    @api_v1_bp.response(200, SubnetSchema(many=True))
    def get(self):
        """List all subnets."""
        return all_networks()

@api_v1_bp.route("/subnets/<path:prefix>/request-ip")
class SubnetRequestIP(MethodView):
    """Request the next available IP in a subnet."""
    @api_v1_bp.arguments(IPRequestSchema)
    @api_v1_bp.response(201, IPRecordSchema)
    def post(self, args, prefix):
        """
        Request the next available IP in a subnet.
        Prefix can be passed as '10.0.0.0/24' or '10.0.0.0_24'.
        """
        cidr = prefix.replace("_", "/")
        net = find_network_by_cidr(cidr)
        if not net:
            abort(404, message=f"Subnet {cidr} not found")

        ip_str = find_next_free_ip(net['id'])
        if not ip_str:
            abort(409, message="No available addresses in this subnet")

        addr_data = {
            'ip': ip_str,
            'hostname': args['hostname'],
            'description': args['description'],
            'status': 'allocated',
            'network_id': net['id']
        }

        if claim_ip_atomic(addr_data, net['cidr'], ttl=args.get('ttl')):
            return addr_data

        abort(409, message="IP was snatched during allocation attempt")

@api_v1_bp.route("/ips/<path:ip_str>")
class IPRecord(MethodView):
    """Operations on individual IP records."""
    @api_v1_bp.response(200, IPRecordSchema)
    def get(self, ip_str):
        """Get IP address details."""
        addr = get_ip(ip_str)
        if not addr:
            abort(404, message="IP not found")
        return addr

@api_v1_bp.route("/stream")
def stream_updates():
    """Real-time update stream (Server-Sent Events)."""
    def event_stream():
        pubsub = r.pubsub()
        pubsub.subscribe('ipam:updates')
        # Send an initial ping
        yield "data: {\"action\": \"ping\"}\n\n"
        try:
            for message in pubsub.listen():
                if message['type'] == 'message':
                    yield f"data: {message['data']}\n\n"
        except (GeneratorExit, Exception):
            # In case of disconnection or error
            try:
                pubsub.unsubscribe('ipam:updates')
            except Exception:  # pylint: disable=broad-except
                pass

    return Response(event_stream(), mimetype="text/event-stream")
