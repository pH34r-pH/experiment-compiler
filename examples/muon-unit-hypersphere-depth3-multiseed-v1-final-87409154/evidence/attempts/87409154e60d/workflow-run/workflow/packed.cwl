{
    "class": "CommandLineTool",
    "id": "#main",
    "requirements": [
        {
            "dockerPull": "docker.io/pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime@sha256:db80a41f8428644cebcb3d75b0b62df334ab6c0e75785951eb25f48bfbd42407",
            "class": "DockerRequirement"
        },
        {
            "networkAccess": false,
            "class": "NetworkAccess"
        },
        {
            "coresMin": 1,
            "coresMax": 1,
            "ramMin": 1280,
            "ramMax": 1280,
            "tmpdirMin": 512,
            "tmpdirMax": 512,
            "outdirMin": 128,
            "outdirMax": 128,
            "class": "ResourceRequirement"
        },
        {
            "timelimit": 3600,
            "class": "ToolTimeLimit"
        }
    ],
    "baseCommand": [
        "python3"
    ],
    "inputs": [
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--byte-checkpoint",
                "separate": true
            },
            "id": "#main/byte"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--issue-123-results",
                "separate": true
            },
            "id": "#main/issue123"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--issue-163-results",
                "separate": true
            },
            "id": "#main/issue163"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--issue-164-results",
                "separate": true
            },
            "id": "#main/issue164"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--preregistration",
                "separate": true
            },
            "id": "#main/preregistration"
        },
        {
            "type": "File",
            "inputBinding": {
                "position": -1
            },
            "id": "#main/runner"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--source-zip",
                "separate": true
            },
            "id": "#main/sourceZip"
        },
        {
            "type": "File",
            "inputBinding": {
                "prefix": "--spectral-checkpoint",
                "separate": true
            },
            "id": "#main/spectral"
        }
    ],
    "arguments": [
        {
            "position": 2,
            "valueFrom": "--output"
        },
        {
            "position": 3,
            "valueFrom": "pilot-result.json"
        }
    ],
    "outputs": [
        {
            "type": "File",
            "outputBinding": {
                "glob": "pilot-result.json"
            },
            "id": "#main/result"
        }
    ],
    "cwlVersion": "v1.2"
}