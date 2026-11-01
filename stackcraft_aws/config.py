"""Read and validate environment-based deployment settings.

When MY_IP_CIDR is blank, this module discovers the operator's current
public IPv4 address so SSH can be limited to that single address.
"""

from dataclasses import dataclass
import ipaddress
import os
from urllib.request import urlopen


def discover_my_ip_cidr():
    """Discover the current public IPv4 address for the SSH ingress rule."""
    try:
        with urlopen("https://checkip.amazonaws.com", timeout=10) as response:
            public_ip = response.read().decode("ascii").strip()
        return f"{ipaddress.IPv4Address(public_ip)}/32"
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(
            "Could not detect your public IPv4 address. Set MY_IP_CIDR to your address with /32."
        ) from exc


def get_my_ip_cidr():
    "Return a validated single-address IPv4 CIDR for the SSH rule."
    # An explicit value wins; otherwise discover the current public IP.
    cidr = os.getenv("MY_IP_CIDR", "").strip() or discover_my_ip_cidr()
    try:
        network = ipaddress.IPv4Network(cidr, strict=False)
    except ValueError as exc:
        raise ValueError("MY_IP_CIDR must be a valid IPv4 CIDR, for example 198.51.100.7/32") from exc
    # SSH access is intentionally limited to one IPv4 address.
    if network.prefixlen != 32:
        raise ValueError("MY_IP_CIDR must use /32 so SSH is restricted to one address")
    return str(network)


@dataclass(frozen=True)
class Config:
    """Immutable settings shared by the deployment modules."""

    region: str
    my_ip_cidr: str
    stack_name: str
    key_pair_file: str

    @classmethod
    def from_env(cls):
        """Build configuration from environment variables and validate the SSH CIDR."""
        region = os.getenv("AWS_REGION", "us-east-1")
        my_ip_cidr = get_my_ip_cidr()
        return cls(
            region=region,
            my_ip_cidr=my_ip_cidr,
            stack_name=os.getenv("STACK_NAME", "Hosting"),
            key_pair_file=os.getenv("KEY_PAIR_FILE", "Hosting-KeyPair.pem"),
        )
