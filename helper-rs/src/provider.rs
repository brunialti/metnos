//! Validation for typed, read-only managed providers.
//!
//! Platform adapters may collect bytes, but only this module decides whether
//! those bytes satisfy the bounded interface returned to an executor.

use serde::{Deserialize, Serialize};

use crate::protocol::{HardwareDomain, SensorKind};

const MAX_SENSORS: usize = 32;
const MAX_TEXT: usize = 160;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RawPayload {
    sensors: Vec<RawSensor>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RawSensor {
    domain: HardwareDomain,
    kind: SensorKind,
    name: String,
    identifier: String,
    value: f64,
    unit: String,
}

fn unit(kind: SensorKind) -> &'static str {
    match kind {
        SensorKind::Voltage => "V",
        SensorKind::Current => "A",
        SensorKind::Power => "W",
        SensorKind::Clock => "MHz",
        SensorKind::Temperature => "°C",
        SensorKind::Load | SensorKind::Control | SensorKind::Level | SensorKind::Humidity => "%",
        SensorKind::Frequency => "Hz",
        SensorKind::Fan => "RPM",
        SensorKind::Flow => "L/h",
        SensorKind::Factor => "",
        SensorKind::Data => "GB",
        SensorKind::SmallData => "MB",
        SensorKind::Throughput => "B/s",
        SensorKind::TimeSpan => "s",
        SensorKind::Timing => "ns",
        SensorKind::Energy => "mWh",
        SensorKind::Noise => "dBA",
        SensorKind::Conductivity => "µS/cm",
    }
}

/// Parse and normalise one selective hardware-sensor v1 result.
pub fn hardware_payload(
    raw: &str,
    domains: &[HardwareDomain],
    sensor_types: &[SensorKind],
) -> Result<serde_json::Value, &'static str> {
    if raw.len() > 64 * 1024 {
        return Err("provider_output_too_large");
    }
    let mut payload: RawPayload =
        serde_json::from_str(raw).map_err(|_| "provider_output_invalid")?;
    if payload.sensors.len() > MAX_SENSORS {
        return Err("provider_output_too_large");
    }
    for sensor in &payload.sensors {
        if sensor.name.is_empty()
            || sensor.identifier.is_empty()
            || sensor.name.len() > MAX_TEXT
            || sensor.identifier.len() > MAX_TEXT
            || !sensor.value.is_finite()
            || !(-1e15..=1e15).contains(&sensor.value)
            || sensor.unit != unit(sensor.kind)
            || !domains.contains(&sensor.domain)
            || !sensor_types.contains(&sensor.kind)
        {
            return Err("provider_output_invalid");
        }
    }
    payload.sensors.sort_by(|left, right| {
        (
            left.domain.as_str(),
            left.kind.as_str(),
            &left.identifier,
            &left.name,
        )
            .cmp(&(
                right.domain.as_str(),
                right.kind.as_str(),
                &right.identifier,
                &right.name,
            ))
            .then_with(|| left.value.total_cmp(&right.value))
    });
    serde_json::to_value(payload).map_err(|_| "provider_output_invalid")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hardware_payload_is_bounded_and_deterministic() {
        let value = hardware_payload(
            r#"{"sensors":[{"domain":"cpu","kind":"temperature","name":"Core 2","identifier":"/cpu/2","value":53.5,"unit":"°C"},{"domain":"cpu","kind":"temperature","name":"Core 1","identifier":"/cpu/1","value":51.0,"unit":"°C"}]}"#,
            &[HardwareDomain::Cpu],
            &[SensorKind::Temperature],
        ).unwrap();
        assert_eq!(value["sensors"][0]["identifier"], "/cpu/1");
        assert!(
            hardware_payload(
                r#"{"sensors":[{"domain":"cpu","kind":"temperature","name":"","identifier":"x","value":1.0,"unit":"°C"}]}"#,
                &[HardwareDomain::Cpu],
                &[SensorKind::Temperature],
            ).is_err()
        );
        assert!(hardware_payload(
            r#"{"sensors":[],"extra":true}"#,
            &[HardwareDomain::Cpu],
            &[SensorKind::Temperature],
        )
        .is_err());
    }
}
