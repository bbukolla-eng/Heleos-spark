use serde::{Deserialize, Serialize};

use crate::{HeleosError, PdfLimits, Result, Sha256Digest, SheetId, page_id};

const JCS_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;

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

pub(crate) const fn expected_rotation_matrix(rotation_degrees: u16) -> Option<(i8, i8, i8, i8)> {
    match rotation_degrees {
        0 => Some((1, 0, 0, -1)),
        90 => Some((0, 1, 1, 0)),
        180 => Some((-1, 0, 0, 1)),
        270 => Some((0, -1, -1, 0)),
        _ => None,
    }
}

pub(crate) fn validate_page_metadata(
    pages: &[PageMetadata],
    content_sha256: Sha256Digest,
    limits: PdfLimits,
) -> Result<()> {
    let page_count = u32::try_from(pages.len()).map_err(|_| HeleosError::Integrity)?;
    if page_count > limits.max_pages {
        return Err(HeleosError::Integrity);
    }
    let axis_cap = u64::from(limits.max_page_axis_points)
        .checked_mul(1_000_000)
        .ok_or(HeleosError::Integrity)?;
    for (ordinal, page) in pages.iter().enumerate() {
        let index = u32::try_from(ordinal).map_err(|_| HeleosError::Integrity)?;
        let matrix =
            expected_rotation_matrix(page.rotation_degrees).ok_or(HeleosError::Integrity)?;
        let transform = page.transform;
        if page.index != index
            || page.page_id != page_id(content_sha256, index)
            || page.width_micropoints == 0
            || page.height_micropoints == 0
            || page.width_micropoints > axis_cap
            || page.height_micropoints > axis_cap
            || page.width_micropoints > JCS_SAFE_INTEGER_MAX
            || page.height_micropoints > JCS_SAFE_INTEGER_MAX
            || transform.tx_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
            || transform.ty_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
            || (transform.m11, transform.m12, transform.m21, transform.m22) != matrix
        {
            return Err(HeleosError::Integrity);
        }
    }
    Ok(())
}
