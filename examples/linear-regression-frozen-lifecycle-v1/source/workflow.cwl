cwlVersion: v1.2
class: CommandLineTool
baseCommand: python3
arguments:
  - position: 1
    valueFrom: $(inputs.reproduce.path)
  - position: 2
    valueFrom: --output-dir
  - position: 3
    valueFrom: $(runtime.outdir)
  - position: 4
    valueFrom: --implementation
  - position: 5
    valueFrom: $(inputs.implementation.path)
  - position: 6
    valueFrom: --tests
  - position: 7
    valueFrom: $(inputs.tests.path)
  - position: 8
    valueFrom: --data
  - position: 9
    valueFrom: $(inputs.data.path)
  - position: 10
    valueFrom: --configuration
  - position: 11
    valueFrom: $(inputs.configuration.path)
  - position: 12
    valueFrom: --acceptance
  - position: 13
    valueFrom: $(inputs.acceptance.path)
  - position: 14
    valueFrom: --environment
  - position: 15
    valueFrom: $(inputs.environment.path)
  - position: 16
    valueFrom: --protocol
  - position: 17
    valueFrom: $(inputs.protocol.path)
  - position: 18
    valueFrom: --expected-result
  - position: 19
    valueFrom: $(inputs.expectedResult.path)
inputs:
  reproduce:
    type: File
  implementation:
    type: File
  tests:
    type: File
  data:
    type: File
  configuration:
    type: File
  acceptance:
    type: File
  expectedResult:
    type: File
  environment:
    type: File
  protocol:
    type: File
requirements:
  DockerRequirement:
    dockerPull: python:3.13.13-slim@sha256:7ba5f5888fbe0014ab9edb2278922995c2201fc3752c46b0be24763eb46fa9f3
  NetworkAccess:
    networkAccess: false
  ResourceRequirement:
    coresMin: 1
    coresMax: 1
    ramMin: 32
    ramMax: 64
    tmpdirMin: 4
    tmpdirMax: 4
    outdirMin: 1
    outdirMax: 1
  ToolTimeLimit:
    timelimit: 120
outputs:
  result:
    type: File
    outputBinding:
      glob: result.json
  reproductionReceipt:
    type: File
    outputBinding:
      glob: reproduction-receipt.json
