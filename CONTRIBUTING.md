# Contributing

Open an issue describing a concrete workflow, ambiguity or reproducible behavior. For protocol changes include the affected draft section, message examples, compatibility consequences and failure/recovery behavior. Separate measured hardware observations from proposed behavior. Do not claim vendor support without public evidence.

Code changes should be small and include relevant tests. Run the README validation commands. Keep schema examples aligned with the prose. Current validation is only a draft consistency check; new transport code needs real packet tests and new hardware code needs named test configurations and results.

Contributions to prose/specification are accepted under CC BY 4.0; code, schemas and machine-readable fixtures under MIT as scoped in LICENSES/README.md. Submit only material you may license. Preserve upstream notices when importing code and document its source. There is no contributor license agreement or required transfer of copyright. Contributions do not establish that third-party patents are licensed.

Review hardware changes for scoped writes, verified readback, ownership loss and capture-path isolation. Never add a blanket mixer reset as a recovery shortcut. Remote listeners must stay disabled by default. No protocol identifiers, vendor allocations or official registrations should be invented for experiments.
