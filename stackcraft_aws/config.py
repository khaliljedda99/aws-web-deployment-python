from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    region: str
    my_ip_cidr: str
    stack_name: str
    key_pair_file: str

    @classmethod
    def from_env(cls):
        region = os.getenv("AWS_REGION", "us-east-1")
        my_ip_cidr = os.getenv("MY_IP_CIDR", "")
        if not my_ip_cidr.endswith("/32"):
            raise ValueError("Set MY_IP_CIDR to your public IPv4 address with /32, e.g. 198.51.100.7/32")
        return cls(region, my_ip_cidr, os.getenv("STACK_NAME", "Hosting"),
                   os.getenv("KEY_PAIR_FILE", "Hosting-KeyPair.pem"))
