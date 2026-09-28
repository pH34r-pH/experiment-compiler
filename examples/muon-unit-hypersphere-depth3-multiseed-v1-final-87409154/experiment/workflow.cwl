cwlVersion: v1.2
class: CommandLineTool
id: muon-seed17-pilot
requirements:
  NetworkAccess:
    networkAccess: false
  DockerRequirement:
    dockerPull: docker.io/pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime@sha256:db80a41f8428644cebcb3d75b0b62df334ab6c0e75785951eb25f48bfbd42407
  ResourceRequirement:
    coresMin: 1
    coresMax: 1
    ramMin: 1280
    ramMax: 1280
    tmpdirMin: 512
    tmpdirMax: 512
    outdirMin: 128
    outdirMax: 128
  ToolTimeLimit:
    timelimit: 3600
baseCommand: [python3]
inputs:
  runner:
    type: File
    inputBinding:
      position: -1
  issue123:
    type: File
    inputBinding:
      prefix: --issue-123-results
      separate: true
  issue163:
    type: File
    inputBinding:
      prefix: --issue-163-results
      separate: true
  issue164:
    type: File
    inputBinding:
      prefix: --issue-164-results
      separate: true
  spectral:
    type: File
    inputBinding:
      prefix: --spectral-checkpoint
      separate: true
  byte:
    type: File
    inputBinding:
      prefix: --byte-checkpoint
      separate: true
  preregistration:
    type: File
    inputBinding:
      prefix: --preregistration
      separate: true
  sourceZip:
    type: File
    inputBinding:
      prefix: --source-zip
      separate: true
arguments:
  - position: 2
    valueFrom: --output
  - position: 3
    valueFrom: pilot-result.json
outputs:
  result:
    type: File
    outputBinding:
      glob: pilot-result.json
