# 🌐 IPAM — IP Address Management System

A **Flask-based IP Address Management (IPAM) web application** backed by Redis. Manage IP addressing, network topology, hardware resources, and external integrations (VMware) with ease.

> **Status:** Production-ready with ongoing enhancements  
> **Python:** 3.8+  
> **License:** See LICENSE file

---

## 📋 Table of Contents

- [Features](#-features)
- [Quick Start](#-quick-start)
- [Architecture](#-architecture)
- [API Reference](#-api-reference)
- [Development](#-development)
- [Testing](#-testing)
- [Configuration](#-configuration)
- [Troubleshooting](#-troubleshooting)

---

## ✨ Features

### Core IPAM
- **Project Management** — Organize IPs into logical projects
- **Subnet Management** — Create, edit, and delete subnets with VLAN support
- **IP Allocation** — Manual and automatic IP allocation with templating
- **Labels & Tags** — Flexible labeling at global and project scopes
- **Pool Queries** — Search and filter available IPs by labels
- **Subnet Templates** — Reusable templates with rule-based slot allocation

### Network Elements (NE)
- **NE Types** — Define and manage network element types
- **Sites & PODs** — Organize infrastructure by location and availability zones
- **Requirements** — Track NE requirements and dependencies

### Hardware (HW)
- **Template System** — HW templates with configurable attributes
- **Bill of Materials (BoM)** — Track components and quantities
- **Instances & Racks** — Manage physical hardware instances
- **Cable Management** — Track cable connections and compatibility

### VMware Integration
- **Subnet Enablement** — Enable/disable subnets for VMware allocation
- **IP Allocation API** — Allocate IPs programmatically for VMs
- **Metadata Tracking** — Record VM, datacenter, and cluster info
- **Release Management** — Return IPs to the pool

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.8+**
- **Redis 5.0+**
- **pip** or **poetry**

### Installation

```bash
# Clone the repository
git clone https://github.com/lucianpopovici/ipam.git
cd ipam

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Install test dependencies
pip install -r requirements-test.txt
