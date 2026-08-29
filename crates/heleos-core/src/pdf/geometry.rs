use serde::{Deserialize, Serialize};

use crate::SheetId;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum PageUnit {
    #[serde(rename = "pt")]
    Point,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PageTransform {
    pub m11: i8,
    pub m12: i8,
    pub m21: i8,
    pub m22: i8,
    pub tx_micropoints: i64,
    pub ty_micropoints: i64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PageMetadata {
    pub index: u32,
    pub page_id: SheetId,
    pub width_micropoints: u64,
    pub height_micropoints: u64,
    pub unit: PageUnit,
    pub rotation_degrees: u16,
    pub transform: PageTransform,
}
