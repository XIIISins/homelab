#!/usr/bin/env python3
"""burst-inventory — turn `terraform output -json` of terraform/digitalocean-burst into the
Ansible inventory for playbooks/burst-k3s.yml.

Usage:  terraform -chdir=terraform/digitalocean-burst output -json | burst-inventory.py OUT.json

Groups: all > burst > k3s > {k3s_cp, k3s_worker}. ansible_host = public IPv4 (SSH runs over it);
k3s_node_ip = VPC private IPv4 (K3s node-ip + join address, so cluster traffic never uses the
public interface). Both K3s groups always exist, even when empty — the k3s role indexes
groups['k3s_worker'] unconditionally. Pure stdlib; the output (JSON is valid for the Ansible
`yaml` inventory plugin) is gitignored.
"""
import json
import sys


def build(outputs):
    droplets = outputs["droplets"]["value"]
    if not droplets:
        raise SystemExit("burst-inventory: terraform output has no droplets (burst_count = 0?)")
    vpc = outputs["vpc_ip_range"]["value"]
    groups = {"k3s_cp": {}, "k3s_worker": {}}
    for d in droplets:
        groups["k3s_cp" if d["role"] == "cp" else "k3s_worker"][d["name"]] = {
            "ansible_host": d["public_ipv4"],
            "k3s_node_ip": d["private_ipv4"],
        }
    # the init node (k3s_init_node = burst-1) must be the first CP, and CPs must be listed first
    if "burst-1" not in groups["k3s_cp"]:
        raise SystemExit("burst-inventory: burst-1 must be a control plane (it is the K3s init node)")
    return {
        "all": {
            "vars": {"k3s_calico_node_cidr": vpc, "burst_ttl_hours": outputs["ttl_hours"]["value"]},
            "children": {
                "burst": {
                    "children": {
                        "k3s": {
                            "children": {
                                "k3s_cp": {"hosts": groups["k3s_cp"]},
                                "k3s_worker": {"hosts": groups["k3s_worker"]},
                            }
                        }
                    }
                }
            },
        }
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    inventory = build(json.load(sys.stdin))
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(inventory, fh, indent=2)
        fh.write("\n")
    print(f"burst-inventory: wrote {sys.argv[1]}")


if __name__ == "__main__":
    main()
