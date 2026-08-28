pub const FOUNDATION_SCHEMA_VERSION: i64 = 1;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntegrityReport {
    pub integrity_check_violations: Vec<String>,
    pub quick_check_violations: Vec<String>,
    pub foreign_key_violations: Vec<String>,
}

impl IntegrityReport {
    pub const fn is_clean(&self) -> bool {
        self.integrity_check_violations.is_empty()
            && self.quick_check_violations.is_empty()
            && self.foreign_key_violations.is_empty()
    }
}
