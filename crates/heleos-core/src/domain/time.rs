use uuid::Uuid;

pub trait Clock {
    fn now_unix_ms(&self) -> i64;
}

pub trait IdGenerator {
    fn next_uuid(&self) -> Uuid;
}
