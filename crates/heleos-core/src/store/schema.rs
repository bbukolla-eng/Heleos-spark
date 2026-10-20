pub const FOUNDATION_SCHEMA_VERSION: i64 = 1;
pub const INTEGRITY_VIOLATION_LIMIT: usize = 128;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IntegrityReport {
    pub integrity_check_violations: Vec<String>,
    pub integrity_check_truncated: bool,
    pub quick_check_violations: Vec<String>,
    pub quick_check_truncated: bool,
    pub foreign_key_violations: Vec<String>,
    pub foreign_key_check_truncated: bool,
}

impl IntegrityReport {
    pub const fn is_clean(&self) -> bool {
        self.integrity_check_violations.is_empty()
            && !self.integrity_check_truncated
            && self.quick_check_violations.is_empty()
            && !self.quick_check_truncated
            && self.foreign_key_violations.is_empty()
            && !self.foreign_key_check_truncated
    }
}
