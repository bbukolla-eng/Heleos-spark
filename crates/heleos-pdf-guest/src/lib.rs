#![forbid(unsafe_code)]

use heleos_pdf_protocol::{
    JCS_SAFE_INTEGER_MAX, JCS_SAFE_INTEGER_MIN, PROTOCOL_VERSION, PageTransformV1, PageUnitV1,
    PdfActiveFeatureV1, PdfDocumentLimitV1, PdfGuestOutcomeV1, PdfGuestReasonV1, PdfPageV1,
    PdfRequestV1, page_id_v1,
};
use lopdf::{Dictionary, Document, LoadOptions, Object, Stream};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashSet};

const MICROPOINTS_PER_POINT: i128 = 1_000_000;

#[derive(Clone, Debug, Eq, PartialEq)]
enum CanonicalEntry {
    Free { next_free: u32, generation: u16 },
    Normal { offset: u32, generation: u16 },
    Compressed { container: u32, index: u32 },
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct CanonicalXref {
    size: u32,
    entries: BTreeMap<u32, CanonicalEntry>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PreflightFailure {
    Encrypted,
    Metadata,
    WorkLimit,
    Corrupt,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReferenceFailure {
    Limit,
    Corrupt,
}

type PdfObjectId = (u32, u16);
type PageTreeParents = BTreeMap<PdfObjectId, Option<PdfObjectId>>;

struct ReferenceWalkContext<'a> {
    document: &'a Document,
    maximum: u32,
    page_tree_parents: Option<&'a PageTreeParents>,
}

#[derive(Debug)]
struct XrefSection {
    size: u32,
    entries: BTreeMap<u32, CanonicalEntry>,
    previous: Option<usize>,
    supplemental: Option<usize>,
    stream_object: Option<(u32, u16, u32)>,
}

#[derive(Debug)]
struct XrefWork {
    rows: u64,
    revisions: u64,
    max_rows: u64,
    max_revisions: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct ObjectStreamState {
    max_metadata_bytes: usize,
    metadata_failure: Option<(u32, u16)>,
    corrupt_failure: Option<(u32, u16)>,
    records: BTreeMap<(u32, u16), Vec<u32>>,
}

thread_local! {
    static OBJECT_STREAM_STATE: RefCell<Option<ObjectStreamState>> = const { RefCell::new(None) };
}

fn reset_object_stream_state(max_metadata_bytes: usize) {
    OBJECT_STREAM_STATE.with(|state| {
        *state.borrow_mut() = Some(ObjectStreamState {
            max_metadata_bytes,
            ..ObjectStreamState::default()
        });
    });
}

fn take_object_stream_state() -> Option<ObjectStreamState> {
    OBJECT_STREAM_STATE.with(|state| state.borrow_mut().take())
}

fn object_stream_filter(id: (u32, u16), object: &mut Object) -> Option<((u32, u16), Object)> {
    let original = object.clone();
    let Object::Stream(stream) = object else {
        return Some((id, original));
    };
    if !matches!(stream.dict.get(b"Type"), Ok(Object::Name(name)) if name == b"ObjStm") {
        return Some((id, original));
    }
    let limit = OBJECT_STREAM_STATE.with(|state| {
        state
            .borrow()
            .as_ref()
            .map(|state| state.max_metadata_bytes)
    });
    let limit = limit?;
    match validate_object_stream(stream, limit) {
        Ok(ids) => {
            OBJECT_STREAM_STATE.with(|state| {
                if let Some(state) = state.borrow_mut().as_mut() {
                    state.records.insert(id, ids);
                }
            });
            Some((id, original))
        }
        Err(PreflightFailure::Metadata) => {
            OBJECT_STREAM_STATE.with(|state| {
                if let Some(state) = state.borrow_mut().as_mut() {
                    record_lowest(&mut state.metadata_failure, id);
                }
            });
            None
        }
        Err(_) => {
            OBJECT_STREAM_STATE.with(|state| {
                if let Some(state) = state.borrow_mut().as_mut() {
                    record_lowest(&mut state.corrupt_failure, id);
                }
            });
            None
        }
    }
}

fn record_lowest(slot: &mut Option<(u32, u16)>, id: (u32, u16)) {
    if slot.is_none_or(|current| id < current) {
        *slot = Some(id);
    }
}

fn validate_object_stream(
    stream: &Stream,
    max_metadata_bytes: usize,
) -> std::result::Result<Vec<u32>, PreflightFailure> {
    if !object_stream_predictor_shape_is_bounded(&stream.dict, max_metadata_bytes)? {
        return Err(PreflightFailure::Corrupt);
    }
    let mut decoded_stream = stream.clone();
    decoded_stream
        .decompress_with_limit(max_metadata_bytes)
        .map_err(classify_decompression_error)?;
    if decoded_stream.content.len() > max_metadata_bytes {
        return Err(PreflightFailure::Metadata);
    }
    let count = dictionary_nonnegative_usize(&decoded_stream.dict, b"N")?;
    let first = dictionary_nonnegative_usize(&decoded_stream.dict, b"First")?;
    let token_count = count.checked_mul(2).ok_or(PreflightFailure::Corrupt)?;
    if token_count > max_metadata_bytes / 2 {
        return Err(PreflightFailure::Corrupt);
    }
    if first > decoded_stream.content.len() {
        return Err(PreflightFailure::Corrupt);
    }
    let header = &decoded_stream.content[..first];
    if token_count > header.len() / 2 {
        return Err(PreflightFailure::Corrupt);
    }
    let mut cursor = 0_usize;
    let mut tokens = Vec::with_capacity(token_count);
    for _ in 0..token_count {
        cursor = skip_ascii_whitespace(header, cursor);
        let start = cursor;
        while cursor < header.len() && header[cursor].is_ascii_digit() {
            cursor += 1;
        }
        if start == cursor || cursor == header.len() || !is_ascii_pdf_whitespace(header[cursor]) {
            return Err(PreflightFailure::Corrupt);
        }
        tokens.push(
            u32::try_from(parse_decimal(&header[start..cursor])?)
                .map_err(|_| PreflightFailure::Corrupt)?,
        );
    }
    if header[cursor..]
        .iter()
        .any(|byte| !is_ascii_pdf_whitespace(*byte))
    {
        return Err(PreflightFailure::Corrupt);
    }

    let mut ids = Vec::with_capacity(count);
    let mut seen_ids = BTreeSet::new();
    let mut previous_offset = None;
    for pair in tokens.chunks_exact(2) {
        let id = pair[0];
        let offset = usize::try_from(pair[1]).map_err(|_| PreflightFailure::Corrupt)?;
        if !seen_ids.insert(id)
            || previous_offset.is_some_and(|previous| offset <= previous)
            || first
                .checked_add(offset)
                .filter(|absolute| *absolute < decoded_stream.content.len())
                .is_none()
        {
            return Err(PreflightFailure::Corrupt);
        }
        previous_offset = Some(offset);
        ids.push(id);
    }
    let parsed = lopdf::ObjectStream::new_with_limit(&mut decoded_stream, Some(max_metadata_bytes))
        .map_err(classify_decompression_error)?;
    if parsed.objects.len() != ids.len()
        || ids.iter().any(|id| !parsed.objects.contains_key(&(*id, 0)))
    {
        return Err(PreflightFailure::Corrupt);
    }
    Ok(ids)
}

fn object_stream_predictor_shape_is_bounded(
    dictionary: &Dictionary,
    maximum: usize,
) -> std::result::Result<bool, PreflightFailure> {
    let predictor_filter = match dictionary.get(b"Filter") {
        Ok(Object::Name(name)) => matches!(name.as_slice(), b"FlateDecode" | b"LZWDecode"),
        Ok(Object::Array(filters)) => filters.iter().any(
            |filter| matches!(filter, Object::Name(name) if matches!(name.as_slice(), b"FlateDecode" | b"LZWDecode")),
        ),
        _ => false,
    };
    if !predictor_filter {
        return Ok(true);
    }
    let parameters = match dictionary.get(b"DecodeParms") {
        Err(_) => return Ok(true),
        Ok(Object::Dictionary(parameters)) => parameters,
        Ok(_) => return Ok(true),
    };
    let predictor = match parameters.get(b"Predictor") {
        Err(_) => 1,
        Ok(Object::Integer(value)) => *value,
        Ok(_) => return Err(PreflightFailure::Corrupt),
    };
    if !(10..=15).contains(&predictor) {
        return Ok(true);
    }
    let parameter = |key: &[u8], minimum: i64| {
        let value = match parameters.get(key) {
            Err(_) => minimum,
            Ok(Object::Integer(value)) => (*value).max(minimum),
            Ok(_) => return Err(PreflightFailure::Corrupt),
        };
        usize::try_from(value).map_err(|_| PreflightFailure::Corrupt)
    };
    let columns = parameter(b"Columns", 1)?;
    let colors = parameter(b"Colors", 1)?;
    let bits = parameter(b"BitsPerComponent", 8)?;
    let bytes_per_pixel = colors
        .checked_mul(bits)
        .and_then(|value| value.checked_div(8))
        .filter(|value| *value > 0)
        .ok_or(PreflightFailure::Corrupt)?;
    Ok(bytes_per_pixel
        .checked_mul(columns)
        .is_some_and(|row_bytes| row_bytes <= maximum))
}

fn skip_ascii_whitespace(bytes: &[u8], mut cursor: usize) -> usize {
    while cursor < bytes.len() && is_ascii_pdf_whitespace(bytes[cursor]) {
        cursor += 1;
    }
    cursor
}

const fn is_ascii_pdf_whitespace(byte: u8) -> bool {
    matches!(byte, 0 | b'\t' | b'\n' | 0x0c | b'\r' | b' ')
}

impl XrefWork {
    fn new(max_indirect_objects: u32) -> Self {
        Self {
            rows: 0,
            revisions: 0,
            max_rows: u64::from(max_indirect_objects) + 1,
            max_revisions: u64::from(max_indirect_objects),
        }
    }

    fn begin_revision(&mut self) -> std::result::Result<(), PreflightFailure> {
        if self.revisions >= self.max_revisions {
            return Err(PreflightFailure::WorkLimit);
        }
        self.revisions += 1;
        Ok(())
    }

    fn examine_row(&mut self) -> std::result::Result<(), PreflightFailure> {
        if self.rows >= self.max_rows {
            return Err(PreflightFailure::WorkLimit);
        }
        self.rows += 1;
        Ok(())
    }
}

fn canonical_xref(
    bytes: &[u8],
    max_indirect_objects: u32,
    max_metadata_bytes: usize,
) -> std::result::Result<CanonicalXref, PreflightFailure> {
    if max_indirect_objects == 0 || max_metadata_bytes == 0 {
        return Err(PreflightFailure::Corrupt);
    }
    let mut next = parse_startxref(bytes)?;
    let mut work = XrefWork::new(max_indirect_objects);
    let mut visited_offsets = BTreeSet::new();
    let mut canonical = BTreeMap::new();
    let mut newest_size = None;

    loop {
        work.begin_revision()?;
        if !visited_offsets.insert(next) {
            return Err(PreflightFailure::Corrupt);
        }
        let mut primary = parse_xref_section(bytes, next, max_metadata_bytes, &mut work)?;
        if newest_size.is_none() {
            newest_size = Some(primary.size);
        }
        let primary_stream_object = primary.stream_object;
        let mut supplemental_stream_object = None;

        if let Some(supplemental_offset) = primary.supplemental {
            if !visited_offsets.insert(supplemental_offset) {
                return Err(PreflightFailure::Corrupt);
            }
            let supplemental =
                parse_xref_stream(bytes, supplemental_offset, max_metadata_bytes, &mut work)?;
            if supplemental.size != primary.size
                || supplemental.supplemental.is_some()
                || supplemental.previous.is_some()
            {
                return Err(PreflightFailure::Corrupt);
            }
            supplemental_stream_object = supplemental.stream_object;
            for (id, entry) in supplemental.entries {
                if primary.entries.insert(id, entry).is_some() {
                    return Err(PreflightFailure::Corrupt);
                }
            }
        }

        for (number, generation, offset) in [primary_stream_object, supplemental_stream_object]
            .into_iter()
            .flatten()
        {
            if primary.entries.get(&number) != Some(&CanonicalEntry::Normal { offset, generation })
            {
                return Err(PreflightFailure::Corrupt);
            }
        }

        for (id, entry) in primary.entries {
            canonical.entry(id).or_insert(entry);
        }
        match primary.previous {
            Some(previous) => next = previous,
            None => break,
        }
    }

    let size = newest_size.ok_or(PreflightFailure::Corrupt)?;
    if size == 0 || canonical.keys().any(|id| *id >= size) {
        return Err(PreflightFailure::Corrupt);
    }
    validate_active_normal_headers(bytes, &canonical)?;
    Ok(CanonicalXref {
        size,
        entries: canonical,
    })
}

fn validate_active_normal_headers(
    bytes: &[u8],
    entries: &BTreeMap<u32, CanonicalEntry>,
) -> std::result::Result<(), PreflightFailure> {
    for (expected_number, entry) in entries {
        let CanonicalEntry::Normal {
            offset,
            generation: expected_generation,
        } = entry
        else {
            continue;
        };
        let mut cursor = usize::try_from(*offset).map_err(|_| PreflightFailure::Corrupt)?;
        if !bytes.get(cursor).is_some_and(u8::is_ascii_digit) {
            return Err(PreflightFailure::Corrupt);
        }
        let number = u32::try_from(parse_unsigned_token(bytes, &mut cursor)?)
            .map_err(|_| PreflightFailure::Corrupt)?;
        let generation = u16::try_from(parse_unsigned_token(bytes, &mut cursor)?)
            .map_err(|_| PreflightFailure::Corrupt)?;
        expect_token(bytes, &mut cursor, b"obj")?;
        if number != *expected_number || generation != *expected_generation {
            return Err(PreflightFailure::Corrupt);
        }
    }
    Ok(())
}

fn parse_xref_section(
    bytes: &[u8],
    offset: usize,
    max_metadata_bytes: usize,
    work: &mut XrefWork,
) -> std::result::Result<XrefSection, PreflightFailure> {
    if offset >= bytes.len() {
        return Err(PreflightFailure::Corrupt);
    }
    if token_at(bytes, offset, b"xref") {
        parse_classic_xref(bytes, offset, max_metadata_bytes, work)
    } else {
        parse_xref_stream(bytes, offset, max_metadata_bytes, work)
    }
}

fn parse_startxref(bytes: &[u8]) -> std::result::Result<usize, PreflightFailure> {
    let mut end = trim_pdf_whitespace_end(bytes, bytes.len());
    end = strip_suffix_at(bytes, end, b"%%EOF")?;
    if end == 0 || !is_pdf_whitespace(bytes[end - 1]) {
        return Err(PreflightFailure::Corrupt);
    }
    end = trim_pdf_whitespace_end(bytes, end);
    let digits_end = end;
    while end > 0 && bytes[end - 1].is_ascii_digit() {
        end -= 1;
    }
    if end == digits_end || end == 0 || !is_pdf_whitespace(bytes[end - 1]) {
        return Err(PreflightFailure::Corrupt);
    }
    let offset = parse_decimal(&bytes[end..digits_end])?;
    end = trim_pdf_whitespace_end(bytes, end);
    end = strip_suffix_at(bytes, end, b"startxref")?;
    if end > 0 && !is_pdf_whitespace(bytes[end - 1]) {
        return Err(PreflightFailure::Corrupt);
    }
    let offset = usize::try_from(offset).map_err(|_| PreflightFailure::Corrupt)?;
    if offset >= bytes.len() {
        return Err(PreflightFailure::Corrupt);
    }
    Ok(offset)
}

fn strip_suffix_at(
    bytes: &[u8],
    end: usize,
    suffix: &[u8],
) -> std::result::Result<usize, PreflightFailure> {
    let start = end
        .checked_sub(suffix.len())
        .ok_or(PreflightFailure::Corrupt)?;
    if bytes.get(start..end) != Some(suffix) {
        return Err(PreflightFailure::Corrupt);
    }
    Ok(start)
}

fn parse_classic_xref(
    bytes: &[u8],
    offset: usize,
    max_metadata_bytes: usize,
    work: &mut XrefWork,
) -> std::result::Result<XrefSection, PreflightFailure> {
    let mut cursor = offset + b"xref".len();
    if cursor >= bytes.len() || !is_pdf_whitespace(bytes[cursor]) {
        return Err(PreflightFailure::Corrupt);
    }
    let mut entries = BTreeMap::new();
    loop {
        cursor = skip_pdf_whitespace(bytes, cursor);
        if token_at(bytes, cursor, b"trailer") {
            cursor += b"trailer".len();
            break;
        }
        let header = next_line(bytes, &mut cursor)?;
        let mut fields = header.split(|byte| is_horizontal_whitespace(*byte));
        let start = fields
            .next()
            .filter(|field| !field.is_empty())
            .ok_or(PreflightFailure::Corrupt)?;
        let count = fields
            .next()
            .filter(|field| !field.is_empty())
            .ok_or(PreflightFailure::Corrupt)?;
        if fields.any(|field| !field.is_empty()) {
            return Err(PreflightFailure::Corrupt);
        }
        let start = u32::try_from(parse_decimal(start)?).map_err(|_| PreflightFailure::Corrupt)?;
        let count = u32::try_from(parse_decimal(count)?).map_err(|_| PreflightFailure::Corrupt)?;
        let end = start.checked_add(count).ok_or(PreflightFailure::Corrupt)?;
        for id in start..end {
            work.examine_row()?;
            let line = next_line(bytes, &mut cursor)?;
            let entry = parse_classic_entry(line)?;
            if entries.insert(id, entry).is_some() {
                return Err(PreflightFailure::Corrupt);
            }
        }
    }

    cursor = skip_pdf_whitespace(bytes, cursor);
    let dictionary_end = dictionary_end(bytes, cursor)?;
    if dictionary_has_top_level_key(&bytes[cursor..dictionary_end], b"Encrypt") == Ok(true) {
        return Err(PreflightFailure::Encrypted);
    }
    if dictionary_end
        .checked_sub(cursor)
        .ok_or(PreflightFailure::Corrupt)?
        > max_metadata_bytes
    {
        return Err(PreflightFailure::Metadata);
    }
    let dictionary = parse_dictionary(&bytes[cursor..dictionary_end])?;
    if dictionary.has(b"Encrypt") {
        return Err(PreflightFailure::Encrypted);
    }
    section_from_dictionary(dictionary, entries, bytes.len(), None)
}

fn parse_classic_entry(line: &[u8]) -> std::result::Result<CanonicalEntry, PreflightFailure> {
    let line = trim_horizontal_end(line);
    if line.len() != 18
        || line[10] != b' '
        || line[16] != b' '
        || !line[..10].iter().all(u8::is_ascii_digit)
        || !line[11..16].iter().all(u8::is_ascii_digit)
    {
        return Err(PreflightFailure::Corrupt);
    }
    let first = parse_decimal(&line[..10])?;
    let generation =
        u16::try_from(parse_decimal(&line[11..16])?).map_err(|_| PreflightFailure::Corrupt)?;
    match line[17] {
        b'f' => Ok(CanonicalEntry::Free {
            next_free: u32::try_from(first).map_err(|_| PreflightFailure::Corrupt)?,
            generation,
        }),
        b'n' => Ok(CanonicalEntry::Normal {
            offset: u32::try_from(first).map_err(|_| PreflightFailure::Corrupt)?,
            generation,
        }),
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn parse_xref_stream(
    bytes: &[u8],
    offset: usize,
    max_metadata_bytes: usize,
    work: &mut XrefWork,
) -> std::result::Result<XrefSection, PreflightFailure> {
    let mut cursor = offset;
    let object_number = u32::try_from(parse_unsigned_token(bytes, &mut cursor)?)
        .map_err(|_| PreflightFailure::Corrupt)?;
    let generation = u16::try_from(parse_unsigned_token(bytes, &mut cursor)?)
        .map_err(|_| PreflightFailure::Corrupt)?;
    let stream_offset = u32::try_from(offset).map_err(|_| PreflightFailure::Corrupt)?;
    expect_token(bytes, &mut cursor, b"obj")?;
    cursor = skip_pdf_whitespace(bytes, cursor);
    let dictionary_end = dictionary_end(bytes, cursor)?;
    let raw_encrypted =
        dictionary_has_top_level_key(&bytes[cursor..dictionary_end], b"Encrypt") == Ok(true);
    if dictionary_end
        .checked_sub(cursor)
        .ok_or(PreflightFailure::Corrupt)?
        > max_metadata_bytes
    {
        return Err(PreflightFailure::Metadata);
    }
    let dictionary = parse_dictionary(&bytes[cursor..dictionary_end])?;
    let encrypted = raw_encrypted || dictionary.has(b"Encrypt");
    if !matches!(dictionary.get(b"Type"), Ok(Object::Name(name)) if name == b"XRef") {
        return Err(PreflightFailure::Corrupt);
    }
    let raw_length = dictionary_nonnegative_usize(&dictionary, b"Length")?;
    if raw_length > max_metadata_bytes {
        return Err(PreflightFailure::Metadata);
    }
    cursor = skip_pdf_whitespace(bytes, dictionary_end);
    if !token_at(bytes, cursor, b"stream") {
        return Err(PreflightFailure::Corrupt);
    }
    cursor += b"stream".len();
    cursor = consume_required_eol(bytes, cursor)?;
    let raw_end = cursor
        .checked_add(raw_length)
        .filter(|end| *end <= bytes.len())
        .ok_or(PreflightFailure::Corrupt)?;
    let raw = bytes
        .get(cursor..raw_end)
        .ok_or(PreflightFailure::Corrupt)?;
    let mut after_stream = raw_end;
    if bytes.get(after_stream) == Some(&b'\r') {
        after_stream += 1;
    }
    if bytes.get(after_stream) == Some(&b'\n') {
        after_stream += 1;
    }
    expect_token(bytes, &mut after_stream, b"endstream")?;
    expect_token(bytes, &mut after_stream, b"endobj")?;

    if !object_stream_predictor_shape_is_bounded(&dictionary, max_metadata_bytes)? {
        return Err(PreflightFailure::Metadata);
    }
    let decoded = if dictionary.has(b"Filter") {
        let stream = Stream::new(dictionary.clone(), raw.to_vec());
        stream
            .decompressed_content_with_limit(max_metadata_bytes)
            .map_err(classify_decompression_error)?
    } else {
        raw.to_vec()
    };
    if decoded.len() > max_metadata_bytes {
        return Err(PreflightFailure::Metadata);
    }

    let size = dictionary_nonnegative_u32(&dictionary, b"Size")?;
    if size == 0 {
        return Err(PreflightFailure::Corrupt);
    }
    let widths = dictionary_widths(&dictionary)?;
    let row_width = widths
        .iter()
        .try_fold(0_usize, |sum, width| sum.checked_add(*width))
        .filter(|width| *width > 0)
        .ok_or(PreflightFailure::Corrupt)?;
    let ranges = dictionary_index_ranges(&dictionary, size)?;
    for (_, count) in &ranges {
        for _ in 0..*count {
            work.examine_row()?;
        }
    }
    if encrypted {
        return Err(PreflightFailure::Encrypted);
    }

    let mut entries = BTreeMap::new();
    let mut position = 0_usize;
    for (start, count) in ranges {
        let end = start.checked_add(count).ok_or(PreflightFailure::Corrupt)?;
        for id in start..end {
            let row_end = position
                .checked_add(row_width)
                .ok_or(PreflightFailure::Corrupt)?;
            let row = decoded
                .get(position..row_end)
                .ok_or(PreflightFailure::Corrupt)?;
            position = row_end;
            let first = if widths[0] == 0 {
                1
            } else {
                decode_big_endian(&row[..widths[0]])?
            };
            let second_end = widths[0] + widths[1];
            let second = decode_big_endian(&row[widths[0]..second_end])?;
            let third = decode_big_endian(&row[second_end..])?;
            let entry = match first {
                0 => CanonicalEntry::Free {
                    next_free: u32::try_from(second).map_err(|_| PreflightFailure::Corrupt)?,
                    generation: u16::try_from(third).map_err(|_| PreflightFailure::Corrupt)?,
                },
                1 => {
                    let offset = u32::try_from(second).map_err(|_| PreflightFailure::Corrupt)?;
                    if usize::try_from(offset).map_err(|_| PreflightFailure::Corrupt)?
                        >= bytes.len()
                    {
                        return Err(PreflightFailure::Corrupt);
                    }
                    CanonicalEntry::Normal {
                        offset,
                        generation: u16::try_from(third).map_err(|_| PreflightFailure::Corrupt)?,
                    }
                }
                2 => CanonicalEntry::Compressed {
                    container: u32::try_from(second).map_err(|_| PreflightFailure::Corrupt)?,
                    index: u32::try_from(third).map_err(|_| PreflightFailure::Corrupt)?,
                },
                _ => return Err(PreflightFailure::Corrupt),
            };
            if entries.insert(id, entry).is_some() {
                return Err(PreflightFailure::Corrupt);
            }
        }
    }
    if position != decoded.len() {
        return Err(PreflightFailure::Corrupt);
    }
    section_from_dictionary(
        dictionary,
        entries,
        bytes.len(),
        Some((object_number, generation, stream_offset)),
    )
}

fn classify_decompression_error(error: lopdf::Error) -> PreflightFailure {
    match error {
        lopdf::Error::Decompress(lopdf::DecompressError::MemoryLimitExceeded { .. }) => {
            PreflightFailure::Metadata
        }
        _ => PreflightFailure::Corrupt,
    }
}

fn section_from_dictionary(
    dictionary: Dictionary,
    entries: BTreeMap<u32, CanonicalEntry>,
    input_length: usize,
    stream_object: Option<(u32, u16, u32)>,
) -> std::result::Result<XrefSection, PreflightFailure> {
    let size = dictionary_nonnegative_u32(&dictionary, b"Size")?;
    if size == 0 || entries.keys().any(|id| *id >= size) {
        return Err(PreflightFailure::Corrupt);
    }
    for entry in entries.values() {
        match entry {
            CanonicalEntry::Free { next_free, .. } if *next_free >= size => {
                return Err(PreflightFailure::Corrupt);
            }
            CanonicalEntry::Normal { offset, .. }
                if usize::try_from(*offset).map_err(|_| PreflightFailure::Corrupt)?
                    >= input_length =>
            {
                return Err(PreflightFailure::Corrupt);
            }
            CanonicalEntry::Compressed { container, .. }
                if *container == 0 || *container >= size =>
            {
                return Err(PreflightFailure::Corrupt);
            }
            _ => {}
        }
    }
    let previous = optional_offset(&dictionary, b"Prev", input_length)?;
    let supplemental = optional_offset(&dictionary, b"XRefStm", input_length)?;
    Ok(XrefSection {
        size,
        entries,
        previous,
        supplemental,
        stream_object,
    })
}

fn optional_offset(
    dictionary: &Dictionary,
    key: &[u8],
    input_length: usize,
) -> std::result::Result<Option<usize>, PreflightFailure> {
    match dictionary.get(key) {
        Err(_) => Ok(None),
        Ok(Object::Integer(value)) if *value >= 0 => {
            let offset = usize::try_from(*value).map_err(|_| PreflightFailure::Corrupt)?;
            if offset >= input_length {
                return Err(PreflightFailure::Corrupt);
            }
            Ok(Some(offset))
        }
        Ok(_) => Err(PreflightFailure::Corrupt),
    }
}

fn dictionary_nonnegative_u32(
    dictionary: &Dictionary,
    key: &[u8],
) -> std::result::Result<u32, PreflightFailure> {
    match dictionary.get(key) {
        Ok(Object::Integer(value)) if *value >= 0 => {
            u32::try_from(*value).map_err(|_| PreflightFailure::Corrupt)
        }
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn dictionary_nonnegative_usize(
    dictionary: &Dictionary,
    key: &[u8],
) -> std::result::Result<usize, PreflightFailure> {
    match dictionary.get(key) {
        Ok(Object::Integer(value)) if *value >= 0 => {
            usize::try_from(*value).map_err(|_| PreflightFailure::Corrupt)
        }
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn dictionary_widths(dictionary: &Dictionary) -> std::result::Result<[usize; 3], PreflightFailure> {
    let Ok(Object::Array(widths)) = dictionary.get(b"W") else {
        return Err(PreflightFailure::Corrupt);
    };
    let [first, second, third] = widths.as_slice() else {
        return Err(PreflightFailure::Corrupt);
    };
    Ok([
        bounded_width(first)?,
        bounded_width(second)?,
        bounded_width(third)?,
    ])
}

fn bounded_width(value: &Object) -> std::result::Result<usize, PreflightFailure> {
    let Object::Integer(value) = value else {
        return Err(PreflightFailure::Corrupt);
    };
    if !(0..=8).contains(value) {
        return Err(PreflightFailure::Corrupt);
    }
    usize::try_from(*value).map_err(|_| PreflightFailure::Corrupt)
}

fn dictionary_index_ranges(
    dictionary: &Dictionary,
    size: u32,
) -> std::result::Result<Vec<(u32, u32)>, PreflightFailure> {
    let values = match dictionary.get(b"Index") {
        Err(_) => return Ok(vec![(0, size)]),
        Ok(Object::Array(values)) if !values.is_empty() && values.len() % 2 == 0 => values,
        _ => return Err(PreflightFailure::Corrupt),
    };
    let mut ranges = Vec::with_capacity(values.len() / 2);
    for pair in values.chunks_exact(2) {
        let start = object_nonnegative_u32(&pair[0])?;
        let count = object_nonnegative_u32(&pair[1])?;
        let end = start.checked_add(count).ok_or(PreflightFailure::Corrupt)?;
        if end > size {
            return Err(PreflightFailure::Corrupt);
        }
        ranges.push((start, count));
    }
    let mut ordered = ranges.clone();
    ordered.sort_by_key(|(start, count)| (*start, *count));
    if ordered.windows(2).any(|pair| {
        pair[0]
            .0
            .checked_add(pair[0].1)
            .is_none_or(|end| end > pair[1].0)
    }) {
        return Err(PreflightFailure::Corrupt);
    }
    Ok(ranges)
}

fn object_nonnegative_u32(value: &Object) -> std::result::Result<u32, PreflightFailure> {
    match value {
        Object::Integer(value) if *value >= 0 => {
            u32::try_from(*value).map_err(|_| PreflightFailure::Corrupt)
        }
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn decode_big_endian(bytes: &[u8]) -> std::result::Result<u64, PreflightFailure> {
    if bytes.len() > 8 {
        return Err(PreflightFailure::Corrupt);
    }
    Ok(bytes
        .iter()
        .fold(0_u64, |value, byte| (value << 8) | u64::from(*byte)))
}

fn parse_dictionary(bytes: &[u8]) -> std::result::Result<Dictionary, PreflightFailure> {
    use lopdf::xref::{Xref, XrefEntry, XrefType};
    use lopdf::{Document, Reader};

    let mut synthetic = b"1 0 obj\n".to_vec();
    synthetic.extend_from_slice(bytes);
    synthetic.extend_from_slice(b"\nendobj\n");
    let mut document = Document::new();
    document.reference_table = Xref::new(2, XrefType::CrossReferenceTable);
    document.reference_table.insert(
        1,
        XrefEntry::Normal {
            offset: 0,
            generation: 0,
        },
    );
    let reader = Reader {
        buffer: &synthetic,
        document,
        encryption_state: None,
        raw_objects: BTreeMap::new(),
        password: None,
        strict: true,
        max_decompressed_size: Some(bytes.len()),
    };
    match reader.get_object((1, 0), &mut HashSet::new()) {
        Ok(Object::Dictionary(dictionary)) => Ok(dictionary),
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn dictionary_end(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    if bytes.get(start..start + 2) != Some(b"<<") {
        return Err(PreflightFailure::Corrupt);
    }
    let mut cursor = start + 2;
    let mut depth = 1_u32;
    while cursor < bytes.len() {
        match bytes[cursor] {
            b'%' => {
                cursor += 1;
                while cursor < bytes.len() && !matches!(bytes[cursor], b'\r' | b'\n') {
                    cursor += 1;
                }
            }
            b'(' => cursor = skip_literal_string(bytes, cursor)?,
            b'<' if bytes.get(cursor + 1) == Some(&b'<') => {
                depth = depth.checked_add(1).ok_or(PreflightFailure::Corrupt)?;
                cursor += 2;
            }
            b'>' if bytes.get(cursor + 1) == Some(&b'>') => {
                depth -= 1;
                cursor += 2;
                if depth == 0 {
                    return Ok(cursor);
                }
            }
            b'<' => cursor = skip_hex_string(bytes, cursor)?,
            _ => cursor += 1,
        }
    }
    Err(PreflightFailure::Corrupt)
}

fn dictionary_has_top_level_key(
    dictionary: &[u8],
    expected: &[u8],
) -> std::result::Result<bool, PreflightFailure> {
    if dictionary.get(..2) != Some(b"<<") {
        return Err(PreflightFailure::Corrupt);
    }
    let mut cursor = 2;
    loop {
        cursor = skip_pdf_space_and_comments(dictionary, cursor);
        if dictionary.get(cursor..cursor.saturating_add(2)) == Some(b">>") {
            return Ok(false);
        }
        let (matches, next) = name_token_matches(dictionary, cursor, expected)?;
        if matches {
            return Ok(true);
        }
        cursor = skip_pdf_value(dictionary, next)?;
    }
}

fn name_token_matches(
    bytes: &[u8],
    start: usize,
    expected: &[u8],
) -> std::result::Result<(bool, usize), PreflightFailure> {
    if bytes.get(start) != Some(&b'/') {
        return Err(PreflightFailure::Corrupt);
    }
    let mut cursor = start + 1;
    let mut expected_index = 0_usize;
    let mut matches = true;
    while cursor < bytes.len()
        && !is_pdf_whitespace(bytes[cursor])
        && !is_pdf_delimiter(bytes[cursor])
    {
        let value = if bytes[cursor] == b'#' {
            let high = *bytes.get(cursor + 1).ok_or(PreflightFailure::Corrupt)?;
            let low = *bytes.get(cursor + 2).ok_or(PreflightFailure::Corrupt)?;
            cursor += 3;
            hex_nibble(high)?
                .checked_mul(16)
                .and_then(|high| high.checked_add(hex_nibble(low).ok()?))
                .ok_or(PreflightFailure::Corrupt)?
        } else {
            let value = bytes[cursor];
            cursor += 1;
            value
        };
        if expected.get(expected_index) != Some(&value) {
            matches = false;
        }
        expected_index = expected_index
            .checked_add(1)
            .ok_or(PreflightFailure::Corrupt)?;
    }
    Ok((matches && expected_index == expected.len(), cursor))
}

fn hex_nibble(byte: u8) -> std::result::Result<u8, PreflightFailure> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn skip_pdf_value(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    let cursor = skip_pdf_space_and_comments(bytes, start);
    match bytes
        .get(cursor)
        .copied()
        .ok_or(PreflightFailure::Corrupt)?
    {
        b'(' => skip_literal_string(bytes, cursor),
        b'<' if bytes.get(cursor + 1) == Some(&b'<') => skip_pdf_compound(bytes, cursor),
        b'<' => skip_hex_string(bytes, cursor),
        b'[' => skip_pdf_compound(bytes, cursor),
        b'/' => name_token_matches(bytes, cursor, b"").map(|(_, cursor)| cursor),
        _ => skip_pdf_atom_or_reference(bytes, cursor),
    }
}

fn skip_pdf_compound(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    let mut cursor = start;
    let mut dictionaries = 0_u32;
    let mut arrays = 0_u32;
    if bytes.get(cursor..cursor.saturating_add(2)) == Some(b"<<") {
        dictionaries = 1;
        cursor += 2;
    } else if bytes.get(cursor) == Some(&b'[') {
        arrays = 1;
        cursor += 1;
    } else {
        return Err(PreflightFailure::Corrupt);
    }
    while cursor < bytes.len() {
        match bytes[cursor] {
            b'%' => cursor = skip_pdf_space_and_comments(bytes, cursor),
            b'(' => cursor = skip_literal_string(bytes, cursor)?,
            b'<' if bytes.get(cursor + 1) == Some(&b'<') => {
                dictionaries = dictionaries
                    .checked_add(1)
                    .ok_or(PreflightFailure::Corrupt)?;
                cursor += 2;
            }
            b'>' if bytes.get(cursor + 1) == Some(&b'>') => {
                dictionaries = dictionaries
                    .checked_sub(1)
                    .ok_or(PreflightFailure::Corrupt)?;
                cursor += 2;
                if dictionaries == 0 && arrays == 0 {
                    return Ok(cursor);
                }
            }
            b'<' => cursor = skip_hex_string(bytes, cursor)?,
            b'[' => {
                arrays = arrays.checked_add(1).ok_or(PreflightFailure::Corrupt)?;
                cursor += 1;
            }
            b']' => {
                arrays = arrays.checked_sub(1).ok_or(PreflightFailure::Corrupt)?;
                cursor += 1;
                if dictionaries == 0 && arrays == 0 {
                    return Ok(cursor);
                }
            }
            _ => cursor += 1,
        }
    }
    Err(PreflightFailure::Corrupt)
}

fn skip_pdf_atom_or_reference(
    bytes: &[u8],
    start: usize,
) -> std::result::Result<usize, PreflightFailure> {
    let first_end = skip_pdf_atom(bytes, start)?;
    if !pdf_integer_token(&bytes[start..first_end]) {
        return Ok(first_end);
    }
    let second_start = skip_pdf_space_and_comments(bytes, first_end);
    let Ok(second_end) = skip_pdf_atom(bytes, second_start) else {
        return Ok(first_end);
    };
    if !pdf_integer_token(&bytes[second_start..second_end]) {
        return Ok(first_end);
    }
    let reference = skip_pdf_space_and_comments(bytes, second_end);
    if token_at(bytes, reference, b"R") {
        Ok(reference + 1)
    } else {
        Ok(first_end)
    }
}

fn skip_pdf_atom(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    let mut cursor = start;
    while cursor < bytes.len()
        && !is_pdf_whitespace(bytes[cursor])
        && !is_pdf_delimiter(bytes[cursor])
    {
        cursor += 1;
    }
    if cursor == start {
        Err(PreflightFailure::Corrupt)
    } else {
        Ok(cursor)
    }
}

fn pdf_integer_token(bytes: &[u8]) -> bool {
    let digits = bytes
        .strip_prefix(b"+")
        .or_else(|| bytes.strip_prefix(b"-"))
        .unwrap_or(bytes);
    !digits.is_empty() && digits.iter().all(u8::is_ascii_digit)
}

fn skip_pdf_space_and_comments(bytes: &[u8], mut cursor: usize) -> usize {
    loop {
        cursor = skip_pdf_whitespace(bytes, cursor);
        if bytes.get(cursor) != Some(&b'%') {
            return cursor;
        }
        cursor += 1;
        while cursor < bytes.len() && !matches!(bytes[cursor], b'\r' | b'\n') {
            cursor += 1;
        }
    }
}

fn skip_literal_string(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    let mut cursor = start + 1;
    let mut depth = 1_u32;
    while cursor < bytes.len() {
        match bytes[cursor] {
            b'\\' => {
                cursor = cursor.checked_add(2).ok_or(PreflightFailure::Corrupt)?;
            }
            b'(' => {
                depth = depth.checked_add(1).ok_or(PreflightFailure::Corrupt)?;
                cursor += 1;
            }
            b')' => {
                depth -= 1;
                cursor += 1;
                if depth == 0 {
                    return Ok(cursor);
                }
            }
            _ => cursor += 1,
        }
    }
    Err(PreflightFailure::Corrupt)
}

fn skip_hex_string(bytes: &[u8], start: usize) -> std::result::Result<usize, PreflightFailure> {
    let mut cursor = start + 1;
    while cursor < bytes.len() {
        if bytes[cursor] == b'>' {
            return Ok(cursor + 1);
        }
        cursor += 1;
    }
    Err(PreflightFailure::Corrupt)
}

fn parse_unsigned_token(
    bytes: &[u8],
    cursor: &mut usize,
) -> std::result::Result<u64, PreflightFailure> {
    *cursor = skip_pdf_whitespace(bytes, *cursor);
    let start = *cursor;
    while *cursor < bytes.len() && bytes[*cursor].is_ascii_digit() {
        *cursor += 1;
    }
    if start == *cursor
        || (*cursor < bytes.len()
            && !is_pdf_whitespace(bytes[*cursor])
            && !is_pdf_delimiter(bytes[*cursor]))
    {
        return Err(PreflightFailure::Corrupt);
    }
    parse_decimal(&bytes[start..*cursor])
}

fn expect_token(
    bytes: &[u8],
    cursor: &mut usize,
    token: &[u8],
) -> std::result::Result<(), PreflightFailure> {
    *cursor = skip_pdf_whitespace(bytes, *cursor);
    if !token_at(bytes, *cursor, token) {
        return Err(PreflightFailure::Corrupt);
    }
    *cursor += token.len();
    Ok(())
}

fn token_at(bytes: &[u8], offset: usize, token: &[u8]) -> bool {
    bytes.get(offset..offset.saturating_add(token.len())) == Some(token)
        && bytes
            .get(offset.saturating_add(token.len()))
            .is_none_or(|byte| is_pdf_whitespace(*byte) || is_pdf_delimiter(*byte))
}

fn next_line<'a>(
    bytes: &'a [u8],
    cursor: &mut usize,
) -> std::result::Result<&'a [u8], PreflightFailure> {
    if *cursor >= bytes.len() {
        return Err(PreflightFailure::Corrupt);
    }
    let start = *cursor;
    while *cursor < bytes.len() && !matches!(bytes[*cursor], b'\r' | b'\n') {
        *cursor += 1;
    }
    if *cursor == bytes.len() {
        return Err(PreflightFailure::Corrupt);
    }
    let line = &bytes[start..*cursor];
    *cursor = consume_required_eol(bytes, *cursor)?;
    Ok(line)
}

fn consume_required_eol(
    bytes: &[u8],
    mut cursor: usize,
) -> std::result::Result<usize, PreflightFailure> {
    match bytes.get(cursor) {
        Some(b'\r') => {
            cursor += 1;
            if bytes.get(cursor) == Some(&b'\n') {
                cursor += 1;
            }
            Ok(cursor)
        }
        Some(b'\n') => Ok(cursor + 1),
        _ => Err(PreflightFailure::Corrupt),
    }
}

fn parse_decimal(bytes: &[u8]) -> std::result::Result<u64, PreflightFailure> {
    if bytes.is_empty() || !bytes.iter().all(u8::is_ascii_digit) {
        return Err(PreflightFailure::Corrupt);
    }
    bytes.iter().try_fold(0_u64, |value, byte| {
        value
            .checked_mul(10)
            .and_then(|value| value.checked_add(u64::from(*byte - b'0')))
            .ok_or(PreflightFailure::Corrupt)
    })
}

fn skip_pdf_whitespace(bytes: &[u8], mut cursor: usize) -> usize {
    while cursor < bytes.len() && is_pdf_whitespace(bytes[cursor]) {
        cursor += 1;
    }
    cursor
}

fn trim_pdf_whitespace_end(bytes: &[u8], mut end: usize) -> usize {
    while end > 0 && is_pdf_whitespace(bytes[end - 1]) {
        end -= 1;
    }
    end
}

fn trim_horizontal_end(mut bytes: &[u8]) -> &[u8] {
    while bytes
        .last()
        .is_some_and(|byte| is_horizontal_whitespace(*byte))
    {
        bytes = &bytes[..bytes.len() - 1];
    }
    bytes
}

const fn is_horizontal_whitespace(byte: u8) -> bool {
    matches!(byte, 0 | b'\t' | 0x0c | b' ')
}

const fn is_pdf_whitespace(byte: u8) -> bool {
    is_horizontal_whitespace(byte) || matches!(byte, b'\r' | b'\n')
}

const fn is_pdf_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%'
    )
}

pub fn inspect_pdf_v1(bytes: &[u8], request: &PdfRequestV1) -> PdfGuestOutcomeV1 {
    let reject = |reason| PdfGuestOutcomeV1::Rejected { reason };
    let declared_length = usize::try_from(request.byte_length).ok();
    let max_input = usize::try_from(request.limits.max_input_bytes).ok();
    if max_input.is_none_or(|limit| bytes.len() > limit) {
        return reject(PdfGuestReasonV1::LimitExceeded(
            PdfDocumentLimitV1::InputBytes,
        ));
    }
    if request.protocol != PROTOCOL_VERSION
        || declared_length != Some(bytes.len())
        || page_id_v1(&request.input_sha256, 0).is_err()
    {
        return reject(PdfGuestReasonV1::Corrupt);
    }
    let magic_end = bytes.len().min(1_024);
    if !bytes[..magic_end]
        .windows(b"%PDF-".len())
        .any(|window| window == b"%PDF-")
    {
        return reject(PdfGuestReasonV1::BadMagic);
    }
    let max_metadata = match usize::try_from(request.limits.max_metadata_bytes) {
        Ok(value) if value > 0 => value,
        _ => {
            return reject(PdfGuestReasonV1::LimitExceeded(
                PdfDocumentLimitV1::MetadataBytes,
            ));
        }
    };
    let canonical = match canonical_xref(bytes, request.limits.max_indirect_objects, max_metadata) {
        Ok(canonical) => canonical,
        Err(PreflightFailure::Encrypted) => return reject(PdfGuestReasonV1::Encrypted),
        Err(PreflightFailure::Metadata) => {
            return reject(PdfGuestReasonV1::LimitExceeded(
                PdfDocumentLimitV1::MetadataBytes,
            ));
        }
        Err(PreflightFailure::WorkLimit) => {
            return reject(PdfGuestReasonV1::LimitExceeded(
                PdfDocumentLimitV1::IndirectObjects,
            ));
        }
        Err(PreflightFailure::Corrupt) => return reject(PdfGuestReasonV1::Corrupt),
    };

    reset_object_stream_state(max_metadata);
    let loaded = Document::load_mem_with_options(
        bytes,
        LoadOptions {
            password: None,
            filter: Some(object_stream_filter),
            strict: true,
            max_decompressed_size: Some(max_metadata),
        },
    );
    let object_stream_state = take_object_stream_state().unwrap_or_default();
    let document = match loaded {
        Ok(document) => {
            if document.trailer.has(b"Encrypt")
                || document.is_encrypted()
                || document.was_encrypted()
            {
                return reject(PdfGuestReasonV1::Encrypted);
            }
            if object_stream_state.metadata_failure.is_some() {
                return reject(PdfGuestReasonV1::LimitExceeded(
                    PdfDocumentLimitV1::MetadataBytes,
                ));
            }
            if object_stream_state.corrupt_failure.is_some() {
                return reject(PdfGuestReasonV1::Corrupt);
            }
            document
        }
        Err(error) => {
            if encryption_error(&error) {
                return reject(PdfGuestReasonV1::Encrypted);
            }
            if object_stream_state.metadata_failure.is_some()
                || matches!(
                    error,
                    lopdf::Error::Decompress(lopdf::DecompressError::MemoryLimitExceeded { .. })
                )
            {
                return reject(PdfGuestReasonV1::LimitExceeded(
                    PdfDocumentLimitV1::MetadataBytes,
                ));
            }
            return reject(PdfGuestReasonV1::Corrupt);
        }
    };

    if audit_xref_bijection(&document, &canonical, &object_stream_state).is_err() {
        return reject(PdfGuestReasonV1::Corrupt);
    }
    let page_tree = match validated_page_tree(&document) {
        Ok(page_tree) => page_tree,
        Err(()) => return reject(PdfGuestReasonV1::Corrupt),
    };
    let reference_validation = validate_reference_depth_with_page_tree(
        &document,
        request.limits.max_nested_references,
        Some(&page_tree.parents),
    );
    if reference_validation == Err(ReferenceFailure::Corrupt) {
        return reject(PdfGuestReasonV1::Corrupt);
    }
    let active_count = canonical
        .entries
        .values()
        .filter(|entry| !matches!(entry, CanonicalEntry::Free { .. }))
        .count();
    if active_count > request.limits.max_indirect_objects as usize {
        return reject(PdfGuestReasonV1::LimitExceeded(
            PdfDocumentLimitV1::IndirectObjects,
        ));
    }
    if reference_validation == Err(ReferenceFailure::Limit) {
        return reject(PdfGuestReasonV1::LimitExceeded(
            PdfDocumentLimitV1::NestedReferences,
        ));
    }

    let mut scan = DocumentScan::default();
    for object in document.objects.values() {
        scan_object(object, &mut scan);
    }
    scan_dictionary(&document.trailer, &mut scan);
    if scan.largest_metadata_value_bytes > request.limits.max_metadata_bytes {
        return reject(PdfGuestReasonV1::LimitExceeded(
            PdfDocumentLimitV1::MetadataBytes,
        ));
    }

    if page_tree.page_ids.len() > request.limits.max_pages as usize {
        return reject(PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::Pages));
    }
    let page_paths = match page_tree.paths() {
        Ok(paths) => paths,
        Err(()) => return reject(PdfGuestReasonV1::Corrupt),
    };

    let mut pages = Vec::with_capacity(page_paths.len());
    let mut invalid_geometry = false;
    let mut axis_limit = false;
    let mut unsupported_unit = false;
    for (index, path) in page_paths.iter().enumerate() {
        match geometry_for_page(
            &document,
            path,
            request.limits.max_nested_references,
            request.limits.max_page_axis_points,
        ) {
            Ok(geometry) => {
                let index = match u32::try_from(index) {
                    Ok(index) => index,
                    Err(_) => {
                        return reject(PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::Pages));
                    }
                };
                let page_id = match page_id_v1(&request.input_sha256, index) {
                    Ok(page_id) => page_id,
                    Err(_) => return reject(PdfGuestReasonV1::Corrupt),
                };
                pages.push(PdfPageV1 {
                    index,
                    page_id,
                    width_micropoints: geometry.width,
                    height_micropoints: geometry.height,
                    unit: PageUnitV1::Point,
                    rotation_degrees: geometry.rotation,
                    transform: geometry.transform,
                });
            }
            Err(GeometryFailure::Invalid) => invalid_geometry = true,
            Err(GeometryFailure::Axis) => axis_limit = true,
            Err(GeometryFailure::UserUnit) => unsupported_unit = true,
        }
    }
    if invalid_geometry {
        return reject(PdfGuestReasonV1::InvalidGeometry);
    }
    if axis_limit {
        return reject(PdfGuestReasonV1::LimitExceeded(
            PdfDocumentLimitV1::PageAxisPoints,
        ));
    }
    if unsupported_unit {
        return reject(PdfGuestReasonV1::UnsupportedUserUnit);
    }
    if let Some(feature) = scan.features.into_iter().next() {
        return reject(PdfGuestReasonV1::ActiveFeature(feature));
    }
    PdfGuestOutcomeV1::Accepted { pages }
}

fn encryption_error(error: &lopdf::Error) -> bool {
    matches!(
        error,
        lopdf::Error::InvalidPassword
            | lopdf::Error::UnsupportedSecurityHandler(_)
            | lopdf::Error::Decryption(_)
    )
}

fn audit_xref_bijection(
    document: &Document,
    canonical: &CanonicalXref,
    object_streams: &ObjectStreamState,
) -> std::result::Result<(), ()> {
    let trailer_size = match document.trailer.get(b"Size") {
        Ok(Object::Integer(size)) if *size >= 0 => u32::try_from(*size).map_err(|_| ())?,
        _ => return Err(()),
    };
    if trailer_size != canonical.size {
        return Err(());
    }
    let mut expected = BTreeSet::new();
    let mut compressed_locations = BTreeSet::new();
    for (number, entry) in &canonical.entries {
        match entry {
            CanonicalEntry::Free { .. } => {
                if document.objects.keys().any(|(id, _)| id == number) {
                    return Err(());
                }
            }
            CanonicalEntry::Normal { generation, .. } => {
                let id = (*number, *generation);
                if !document.objects.contains_key(&id) || !expected.insert(id) {
                    return Err(());
                }
            }
            CanonicalEntry::Compressed { container, index } => {
                let id = (*number, 0);
                let index = usize::try_from(*index).map_err(|_| ())?;
                let container_entry = canonical.entries.get(container).ok_or(())?;
                let CanonicalEntry::Normal {
                    generation: container_generation,
                    ..
                } = container_entry
                else {
                    return Err(());
                };
                let container_id = (*container, *container_generation);
                if !document.objects.contains_key(&container_id)
                    || !document.objects.contains_key(&id)
                    || !compressed_locations.insert((*container, index))
                    || !expected.insert(id)
                    || object_streams
                        .records
                        .get(&container_id)
                        .and_then(|ids| ids.get(index))
                        != Some(number)
                {
                    return Err(());
                }
            }
        }
    }
    if expected.len() != document.objects.len()
        || document.objects.keys().any(|id| !expected.contains(id))
    {
        return Err(());
    }
    Ok(())
}

#[cfg(test)]
fn validate_reference_depth(
    document: &Document,
    maximum: u32,
) -> std::result::Result<(), ReferenceFailure> {
    let page_tree = validated_page_tree(document).ok();
    validate_reference_depth_with_page_tree(
        document,
        maximum,
        page_tree.as_ref().map(|page_tree| &page_tree.parents),
    )
}

fn validate_reference_depth_with_page_tree(
    document: &Document,
    maximum: u32,
    page_tree_parents: Option<&PageTreeParents>,
) -> std::result::Result<(), ReferenceFailure> {
    let context = ReferenceWalkContext {
        document,
        maximum,
        page_tree_parents,
    };
    let mut result = Ok(());
    for (id, object) in &document.objects {
        let mut active = BTreeSet::from([*id]);
        merge_reference_result(
            &mut result,
            walk_reference_depth(&context, object, 0, &mut active, Some(*id), true),
        );
        if result == Err(ReferenceFailure::Corrupt) {
            return result;
        }
    }
    let mut active = BTreeSet::new();
    merge_reference_result(
        &mut result,
        walk_reference_depth(
            &context,
            &Object::Dictionary(document.trailer.clone()),
            0,
            &mut active,
            None,
            true,
        ),
    );
    result
}

fn merge_reference_result(
    aggregate: &mut std::result::Result<(), ReferenceFailure>,
    next: std::result::Result<(), ReferenceFailure>,
) {
    if next == Err(ReferenceFailure::Corrupt)
        || (*aggregate == Ok(()) && next == Err(ReferenceFailure::Limit))
    {
        *aggregate = next;
    }
}

fn walk_reference_depth(
    context: &ReferenceWalkContext<'_>,
    object: &Object,
    depth: u32,
    active: &mut BTreeSet<PdfObjectId>,
    current_id: Option<PdfObjectId>,
    object_root: bool,
) -> std::result::Result<(), ReferenceFailure> {
    match object {
        Object::Reference(id) => {
            let next_depth = depth.checked_add(1).ok_or(ReferenceFailure::Limit)?;
            if next_depth > context.maximum {
                return Err(ReferenceFailure::Limit);
            }
            if active.contains(id) {
                return Ok(());
            }
            let target = context
                .document
                .objects
                .get(id)
                .ok_or(ReferenceFailure::Corrupt)?;
            active.insert(*id);
            let result = walk_reference_depth(context, target, next_depth, active, Some(*id), true);
            active.remove(id);
            result
        }
        Object::Array(values) => {
            let mut result = Ok(());
            for value in values {
                merge_reference_result(
                    &mut result,
                    walk_reference_depth(context, value, depth, active, current_id, false),
                );
                if result == Err(ReferenceFailure::Corrupt) {
                    break;
                }
            }
            result
        }
        Object::Dictionary(dictionary) => {
            let page_tree_node = object_root
                && current_id.is_some_and(|id| {
                    context
                        .page_tree_parents
                        .is_some_and(|parents| parents.contains_key(&id))
                });
            let mut result = Ok(());
            for (key, value) in dictionary.iter() {
                if page_tree_node && key == b"Parent" {
                    continue;
                }
                merge_reference_result(
                    &mut result,
                    walk_reference_depth(context, value, depth, active, current_id, false),
                );
                if result == Err(ReferenceFailure::Corrupt) {
                    break;
                }
            }
            result
        }
        Object::Stream(stream) => {
            let mut result = Ok(());
            for (_key, value) in stream.dict.iter() {
                merge_reference_result(
                    &mut result,
                    walk_reference_depth(context, value, depth, active, current_id, false),
                );
                if result == Err(ReferenceFailure::Corrupt) {
                    break;
                }
            }
            result
        }
        _ => Ok(()),
    }
}

#[derive(Default)]
struct DocumentScan {
    largest_metadata_value_bytes: u64,
    features: BTreeSet<PdfActiveFeatureV1>,
}

fn scan_object(object: &Object, scan: &mut DocumentScan) {
    match object {
        Object::Name(name) => scan_name(name, scan),
        Object::String(bytes, _) => observe_metadata_value(bytes.len(), scan),
        Object::Array(values) => {
            for value in values {
                scan_object(value, scan);
            }
        }
        Object::Dictionary(dictionary) => scan_dictionary(dictionary, scan),
        Object::Stream(stream) => scan_dictionary(&stream.dict, scan),
        _ => {}
    }
}

fn scan_dictionary(dictionary: &Dictionary, scan: &mut DocumentScan) {
    for (key, value) in dictionary.iter() {
        scan_name(key, scan);
        scan_object(value, scan);
    }
}

fn observe_metadata_value(length: usize, scan: &mut DocumentScan) {
    scan.largest_metadata_value_bytes = scan
        .largest_metadata_value_bytes
        .max(u64::try_from(length).unwrap_or(u64::MAX));
}

fn scan_name(name: &[u8], scan: &mut DocumentScan) {
    observe_metadata_value(name.len(), scan);
    let feature = match name {
        b"OpenAction" => Some(PdfActiveFeatureV1::OpenAction),
        b"AA" => Some(PdfActiveFeatureV1::AdditionalActions),
        b"JS" => Some(PdfActiveFeatureV1::JavaScriptAbbreviation),
        b"JavaScript" => Some(PdfActiveFeatureV1::JavaScript),
        b"Launch" => Some(PdfActiveFeatureV1::Launch),
        b"URI" => Some(PdfActiveFeatureV1::Uri),
        b"GoToR" => Some(PdfActiveFeatureV1::GoToRemote),
        b"SubmitForm" => Some(PdfActiveFeatureV1::SubmitForm),
        b"ImportData" => Some(PdfActiveFeatureV1::ImportData),
        b"RichMedia" => Some(PdfActiveFeatureV1::RichMedia),
        b"EmbeddedFiles" => Some(PdfActiveFeatureV1::EmbeddedFiles),
        b"AF" => Some(PdfActiveFeatureV1::AssociatedFiles),
        b"XFA" => Some(PdfActiveFeatureV1::Xfa),
        b"AcroForm" => Some(PdfActiveFeatureV1::AcroForm),
        _ => None,
    };
    if let Some(feature) = feature {
        scan.features.insert(feature);
    }
}

struct ValidatedPageTree {
    parents: PageTreeParents,
    page_ids: Vec<(u32, u16)>,
}

impl ValidatedPageTree {
    fn paths(&self) -> std::result::Result<Vec<Vec<(u32, u16)>>, ()> {
        let mut paths = Vec::with_capacity(self.page_ids.len());
        for id in &self.page_ids {
            let mut path = Vec::new();
            let mut current = Some(*id);
            while let Some(node) = current {
                path.push(node);
                current = *self.parents.get(&node).ok_or(())?;
            }
            path.reverse();
            paths.push(path);
        }
        Ok(paths)
    }
}

#[cfg(test)]
fn validated_page_paths(
    document: &Document,
    collect_paths: bool,
) -> std::result::Result<Vec<Vec<(u32, u16)>>, ()> {
    let page_tree = validated_page_tree(document)?;
    if collect_paths {
        page_tree.paths()
    } else {
        Ok(Vec::new())
    }
}

fn validated_page_tree(document: &Document) -> std::result::Result<ValidatedPageTree, ()> {
    enum Operation {
        Enter {
            id: (u32, u16),
            parent: Option<(u32, u16)>,
        },
        Exit {
            id: (u32, u16),
            declared_count: u32,
            kids: Vec<(u32, u16)>,
        },
    }

    let root_id = object_reference(document.trailer.get(b"Root").map_err(|_| ())?)?;
    let catalog = object_dictionary(document.objects.get(&root_id).ok_or(())?)?;
    if !dictionary_type_is(catalog, b"Catalog") {
        return Err(());
    }
    let pages_id = object_reference(catalog.get(b"Pages").map_err(|_| ())?)?;
    let mut parents = BTreeMap::new();
    let mut counts = BTreeMap::new();
    let mut operations = vec![Operation::Enter {
        id: pages_id,
        parent: None,
    }];
    let mut page_ids = Vec::new();
    let mut total_pages = 0_u32;
    while let Some(operation) = operations.pop() {
        match operation {
            Operation::Enter { id, parent } => {
                if parents.insert(id, parent).is_some() {
                    return Err(());
                }
                let dictionary = object_dictionary(document.objects.get(&id).ok_or(())?)?;
                if let Some(parent) = parent
                    && object_reference(dictionary.get(b"Parent").map_err(|_| ())?)? != parent
                {
                    return Err(());
                }
                if parent.is_none() && dictionary.has(b"Parent") {
                    return Err(());
                }
                if dictionary_type_is(dictionary, b"Pages") {
                    let kids = match dictionary.get(b"Kids") {
                        Ok(Object::Array(kids)) => kids
                            .iter()
                            .map(object_reference)
                            .collect::<std::result::Result<Vec<_>, _>>()?,
                        _ => return Err(()),
                    };
                    let declared_count = match dictionary.get(b"Count") {
                        Ok(Object::Integer(count)) if *count >= 0 => {
                            u32::try_from(*count).map_err(|_| ())?
                        }
                        _ => return Err(()),
                    };
                    operations.push(Operation::Exit {
                        id,
                        declared_count,
                        kids: kids.clone(),
                    });
                    for kid in kids.into_iter().rev() {
                        operations.push(Operation::Enter {
                            id: kid,
                            parent: Some(id),
                        });
                    }
                } else if dictionary_type_is(dictionary, b"Page") {
                    total_pages = total_pages.checked_add(1).ok_or(())?;
                    counts.insert(id, 1_u32);
                    page_ids.push(id);
                } else {
                    return Err(());
                }
            }
            Operation::Exit {
                id,
                declared_count,
                kids,
            } => {
                let mut actual_count = 0_u32;
                for kid in kids {
                    actual_count = actual_count
                        .checked_add(*counts.get(&kid).ok_or(())?)
                        .ok_or(())?;
                }
                if actual_count != declared_count {
                    return Err(());
                }
                counts.insert(id, actual_count);
            }
        }
    }
    if counts.get(&pages_id).copied() != Some(total_pages) {
        return Err(());
    }
    Ok(ValidatedPageTree { parents, page_ids })
}

fn geometry_for_page(
    document: &Document,
    path: &[(u32, u16)],
    max_depth: u32,
    max_axis_points: u32,
) -> std::result::Result<ResolvedGeometry, GeometryFailure> {
    let media =
        inherited_value(document, path, b"MediaBox", max_depth)?.ok_or(GeometryFailure::Invalid)?;
    let crop = inherited_value(document, path, b"CropBox", max_depth)?;
    let rotate = inherited_value(document, path, b"Rotate", max_depth)?;
    let user_unit = inherited_value(document, path, b"UserUnit", max_depth)?;
    let media = object_box(media)?;
    let crop = crop.map(object_box).transpose()?;
    resolved_geometry(
        &media,
        crop.as_ref(),
        rotate.as_ref(),
        user_unit.as_ref(),
        max_axis_points,
    )
}

fn inherited_value(
    document: &Document,
    path: &[(u32, u16)],
    key: &[u8],
    max_depth: u32,
) -> std::result::Result<Option<Object>, GeometryFailure> {
    for id in path.iter().rev() {
        let dictionary =
            object_dictionary(document.objects.get(id).ok_or(GeometryFailure::Invalid)?)
                .map_err(|_| GeometryFailure::Invalid)?;
        if let Ok(value) = dictionary.get(key) {
            return resolve_owned(document, value.clone(), max_depth).map(Some);
        }
    }
    Ok(None)
}

fn resolve_owned(
    document: &Document,
    mut object: Object,
    max_depth: u32,
) -> std::result::Result<Object, GeometryFailure> {
    let mut active = BTreeSet::new();
    for depth in 0..=max_depth {
        let Object::Reference(id) = object else {
            return Ok(object);
        };
        if depth == max_depth || !active.insert(id) {
            return Err(GeometryFailure::Invalid);
        }
        object = document
            .objects
            .get(&id)
            .cloned()
            .ok_or(GeometryFailure::Invalid)?;
    }
    Err(GeometryFailure::Invalid)
}

fn object_box(object: Object) -> std::result::Result<[Object; 4], GeometryFailure> {
    let Object::Array(values) = object else {
        return Err(GeometryFailure::Invalid);
    };
    values.try_into().map_err(|_| GeometryFailure::Invalid)
}

fn object_reference(object: &Object) -> std::result::Result<(u32, u16), ()> {
    match object {
        Object::Reference(id) => Ok(*id),
        _ => Err(()),
    }
}

fn object_dictionary(object: &Object) -> std::result::Result<&Dictionary, ()> {
    match object {
        Object::Dictionary(dictionary) => Ok(dictionary),
        _ => Err(()),
    }
}

fn dictionary_type_is(dictionary: &Dictionary, expected: &[u8]) -> bool {
    matches!(dictionary.get(b"Type"), Ok(Object::Name(name)) if name == expected)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum GeometryFailure {
    Invalid,
    Axis,
    UserUnit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ResolvedGeometry {
    width: u64,
    height: u64,
    rotation: u16,
    transform: PageTransformV1,
}

fn resolved_geometry(
    media_box: &[Object; 4],
    crop_box: Option<&[Object; 4]>,
    rotate: Option<&Object>,
    user_unit: Option<&Object>,
    max_axis_points: u32,
) -> std::result::Result<ResolvedGeometry, GeometryFailure> {
    let media = convert_box(media_box)?;
    validate_box(media)?;
    let effective = match crop_box {
        Some(crop_box) => {
            let crop = convert_box(crop_box)?;
            validate_box(crop)?;
            if crop[0] < media[0] || crop[1] < media[1] || crop[2] > media[2] || crop[3] > media[3]
            {
                return Err(GeometryFailure::Invalid);
            }
            crop
        }
        None => media,
    };
    let rotation = normalize_rotation(rotate)?;
    let raw_width = effective[2]
        .checked_sub(effective[0])
        .ok_or(GeometryFailure::Invalid)?;
    let raw_height = effective[3]
        .checked_sub(effective[1])
        .ok_or(GeometryFailure::Invalid)?;
    let raw_width = u64::try_from(raw_width).map_err(|_| GeometryFailure::Invalid)?;
    let raw_height = u64::try_from(raw_height).map_err(|_| GeometryFailure::Invalid)?;
    let axis_limit = u64::from(max_axis_points)
        .checked_mul(1_000_000)
        .ok_or(GeometryFailure::Axis)?;
    if raw_width > axis_limit || raw_height > axis_limit {
        return Err(GeometryFailure::Axis);
    }
    validate_user_unit(user_unit)?;

    let [llx, lly, urx, ury] = effective;
    let (width, height) = if matches!(rotation, 90 | 270) {
        (raw_height, raw_width)
    } else {
        (raw_width, raw_height)
    };
    let (m11, m12, m21, m22, tx, ty) = match rotation {
        0 => (1, 0, 0, -1, checked_neg(llx)?, ury),
        90 => (0, 1, 1, 0, checked_neg(lly)?, checked_neg(llx)?),
        180 => (-1, 0, 0, 1, urx, checked_neg(lly)?),
        270 => (0, -1, -1, 0, ury, urx),
        _ => return Err(GeometryFailure::Invalid),
    };
    if !jcs_safe_i64(tx) || !jcs_safe_i64(ty) {
        return Err(GeometryFailure::Invalid);
    }
    Ok(ResolvedGeometry {
        width,
        height,
        rotation,
        transform: PageTransformV1 {
            m11,
            m12,
            m21,
            m22,
            tx_micropoints: tx,
            ty_micropoints: ty,
        },
    })
}

fn convert_box(values: &[Object; 4]) -> std::result::Result<[i64; 4], GeometryFailure> {
    Ok([
        number_to_micropoints(&values[0])?,
        number_to_micropoints(&values[1])?,
        number_to_micropoints(&values[2])?,
        number_to_micropoints(&values[3])?,
    ])
}

fn number_to_micropoints(value: &Object) -> std::result::Result<i64, GeometryFailure> {
    let converted = match value {
        Object::Integer(value) => i128::from(*value)
            .checked_mul(MICROPOINTS_PER_POINT)
            .and_then(|value| i64::try_from(value).ok())
            .ok_or(GeometryFailure::Invalid)?,
        Object::Real(value) => {
            let scaled = f64::from(*value) * MICROPOINTS_PER_POINT as f64;
            if !scaled.is_finite() {
                return Err(GeometryFailure::Invalid);
            }
            let rounded = scaled.round_ties_even();
            if rounded < i64::MIN as f64 || rounded > i64::MAX as f64 {
                return Err(GeometryFailure::Invalid);
            }
            rounded as i64
        }
        _ => return Err(GeometryFailure::Invalid),
    };
    if !jcs_safe_i64(converted) {
        return Err(GeometryFailure::Invalid);
    }
    Ok(converted)
}

fn validate_box(value: [i64; 4]) -> std::result::Result<(), GeometryFailure> {
    if value[0] >= value[2] || value[1] >= value[3] {
        return Err(GeometryFailure::Invalid);
    }
    Ok(())
}

fn normalize_rotation(value: Option<&Object>) -> std::result::Result<u16, GeometryFailure> {
    let value = match value {
        None => 0,
        Some(Object::Integer(value)) => *value,
        Some(_) => return Err(GeometryFailure::Invalid),
    };
    if value % 90 != 0 {
        return Err(GeometryFailure::Invalid);
    }
    u16::try_from(value.rem_euclid(360)).map_err(|_| GeometryFailure::Invalid)
}

fn validate_user_unit(value: Option<&Object>) -> std::result::Result<(), GeometryFailure> {
    match value {
        None | Some(Object::Integer(1)) => Ok(()),
        Some(Object::Real(value)) if value.is_finite() && *value == 1.0 => Ok(()),
        Some(_) => Err(GeometryFailure::UserUnit),
    }
}

fn checked_neg(value: i64) -> std::result::Result<i64, GeometryFailure> {
    value.checked_neg().ok_or(GeometryFailure::Invalid)
}

const fn jcs_safe_i64(value: i64) -> bool {
    value >= JCS_SAFE_INTEGER_MIN && value <= JCS_SAFE_INTEGER_MAX as i64
}

#[cfg(test)]
mod tests {
    use heleos_pdf_protocol::{
        JCS_SAFE_INTEGER_MAX, JCS_SAFE_INTEGER_MIN, PROTOCOL_VERSION, PdfActiveFeatureV1,
        PdfDocumentLimitV1, PdfDocumentLimitsV1, PdfGuestOutcomeV1, PdfGuestReasonV1, PdfRequestV1,
    };
    use lopdf::{Document, Object};

    use super::{
        CanonicalEntry, GeometryFailure, PreflightFailure, canonical_xref, inspect_pdf_v1,
        jcs_safe_i64, object_stream_filter, reset_object_stream_state, resolved_geometry,
        take_object_stream_state, validate_object_stream, validate_reference_depth,
        validated_page_paths,
    };

    fn integers(values: [i64; 4]) -> [Object; 4] {
        values.map(Object::Integer)
    }

    #[test]
    fn geometry_uses_nonzero_origin_and_all_frozen_rotation_matrices() {
        let expected = [
            (
                0,
                300_000_000,
                400_000_000,
                (1, 0, 0, -1, -10_000_000, 420_000_000),
            ),
            (
                90,
                400_000_000,
                300_000_000,
                (0, 1, 1, 0, -20_000_000, -10_000_000),
            ),
            (
                180,
                300_000_000,
                400_000_000,
                (-1, 0, 0, 1, 310_000_000, -20_000_000),
            ),
            (
                270,
                400_000_000,
                300_000_000,
                (0, -1, -1, 0, 420_000_000, 310_000_000),
            ),
        ];
        for (rotation, width, height, matrix) in expected {
            let geometry = resolved_geometry(
                &integers([0, 0, 612, 792]),
                Some(&integers([10, 20, 310, 420])),
                Some(&Object::Integer(rotation)),
                None,
                1_000,
            )
            .expect("geometry resolves");
            assert_eq!((geometry.width, geometry.height), (width, height));
            assert_eq!(
                (
                    geometry.transform.m11,
                    geometry.transform.m12,
                    geometry.transform.m21,
                    geometry.transform.m22,
                    geometry.transform.tx_micropoints,
                    geometry.transform.ty_micropoints,
                ),
                matrix
            );
        }
    }

    #[test]
    fn geometry_normalizes_negative_rotation_and_rounds_f32_ties_even() {
        let geometry = resolved_geometry(
            &[
                Object::Real(0.000_000_5),
                Object::Integer(0),
                Object::Real(10.000_002),
                Object::Integer(20),
            ],
            None,
            Some(&Object::Integer(-90)),
            Some(&Object::Real(1.0)),
            100,
        )
        .expect("fractional geometry resolves");
        assert_eq!(geometry.rotation, 270);
        assert_eq!(geometry.transform.ty_micropoints, 10_000_002);
    }

    #[test]
    fn geometry_distinguishes_box_axis_and_user_unit_failures() {
        assert_eq!(
            resolved_geometry(&integers([0, 0, 0, 10]), None, None, None, 100),
            Err(GeometryFailure::Invalid)
        );
        assert_eq!(
            resolved_geometry(&integers([0, 0, 101, 10]), None, None, None, 100),
            Err(GeometryFailure::Axis)
        );
        assert_eq!(
            resolved_geometry(
                &integers([0, 0, 10, 10]),
                None,
                None,
                Some(&Object::Real(2.0)),
                100,
            ),
            Err(GeometryFailure::UserUnit)
        );
        assert_eq!(
            resolved_geometry(
                &integers([0, 0, 10, 10]),
                Some(&integers([-1, 0, 9, 10])),
                None,
                None,
                100,
            ),
            Err(GeometryFailure::Invalid)
        );
    }

    #[test]
    fn geometry_binds_jcs_safe_translation_endpoints_and_one_over() {
        assert!(jcs_safe_i64(JCS_SAFE_INTEGER_MIN));
        assert!(jcs_safe_i64(JCS_SAFE_INTEGER_MAX as i64));
        assert!(!jcs_safe_i64(JCS_SAFE_INTEGER_MIN - 1));
        assert!(!jcs_safe_i64(JCS_SAFE_INTEGER_MAX as i64 + 1));

        let nearest_integer_point_origin = resolved_geometry(
            &integers([-9_007_199_254, 0, -9_007_199_253, 1]),
            None,
            None,
            None,
            1,
        )
        .expect("the largest whole-point origin below the JCS endpoint resolves");
        assert_eq!(
            nearest_integer_point_origin.transform.tx_micropoints,
            9_007_199_254_000_000
        );
        assert_eq!(
            resolved_geometry(
                &integers([-9_007_199_255, 0, -9_007_199_254, 1]),
                None,
                None,
                None,
                1,
            ),
            Err(GeometryFailure::Invalid)
        );
    }

    fn startxref(bytes: &[u8]) -> usize {
        let marker = b"startxref\n";
        let position = bytes
            .windows(marker.len())
            .rposition(|window| window == marker)
            .expect("fixture has startxref");
        let digits = &bytes[position + marker.len()..];
        let end = digits
            .iter()
            .position(|byte| *byte == b'\n')
            .expect("startxref line ends");
        std::str::from_utf8(&digits[..end])
            .expect("startxref is ASCII")
            .parse()
            .expect("startxref is decimal")
    }

    fn append_revision(base: &[u8], xref_body: &[u8], trailer_extra: &str) -> Vec<u8> {
        let previous = startxref(base);
        let mut bytes = base.to_vec();
        let offset = bytes.len();
        bytes.extend_from_slice(b"xref\n");
        bytes.extend_from_slice(xref_body);
        bytes.extend_from_slice(
            format!(
                "trailer\n<< /Size 6 /Root 1 0 R /Prev {previous} {trailer_extra} >>\nstartxref\n{offset}\n%%EOF\n"
            )
            .as_bytes(),
        );
        bytes
    }

    fn xref_stream(entry_type: u8, raw_padding: usize) -> Vec<u8> {
        let mut rows = vec![0, 0, 0, 0, entry_type, 0, 9, 0];
        rows.extend(std::iter::repeat_n(0, raw_padding));
        let mut bytes = b"%PDF-1.7\n".to_vec();
        let offset = bytes.len();
        bytes.extend_from_slice(
            format!(
                "1 0 obj\n<< /Type /XRef /Size 2 /W [1 2 1] /Index [0 2] /Length {} >>\nstream\n",
                rows.len()
            )
            .as_bytes(),
        );
        bytes.extend_from_slice(&rows);
        bytes.extend_from_slice(b"\nendstream\nendobj\n");
        bytes.extend_from_slice(format!("startxref\n{offset}\n%%EOF\n").as_bytes());
        bytes
    }

    fn encrypted_xref_stream() -> Vec<u8> {
        let rows = [0, 0, 0, 0, 1, 0, 9, 0, 0, 0, 0, 0];
        let mut bytes = b"%PDF-1.7\n".to_vec();
        let offset = bytes.len();
        bytes.extend_from_slice(
            format!(
                "1 0 obj\n<< /Type /XRef /Size 3 /W [1 2 1] /Index [0 3] /Encrypt -9 /Length {} >>\nstream\n",
                rows.len()
            )
            .as_bytes(),
        );
        bytes.extend_from_slice(&rows);
        bytes.extend_from_slice(b"\nendstream\nendobj\n");
        bytes.extend_from_slice(format!("startxref\n{offset}\n%%EOF\n").as_bytes());
        bytes
    }

    fn filtered_xref_stream(columns: i64) -> Vec<u8> {
        let mut compressed =
            lopdf::Stream::new(lopdf::Dictionary::new(), vec![0, 0, 0, 0, 0, 1, 0, 9, 0]);
        compressed.compress().expect("xref rows compress");
        let raw = compressed.content;
        let mut bytes = b"%PDF-1.7\n".to_vec();
        let offset = bytes.len();
        bytes.extend_from_slice(
            format!(
                "1 0 obj\n<< /Type /XRef /Size 2 /W [1 2 1] /Index [0 2] /Filter /FlateDecode /DecodeParms << /Predictor 15 /Columns {columns} /Colors 1 /BitsPerComponent 8 >> /Length {} >>\nstream\n",
                raw.len()
            )
            .as_bytes(),
        );
        bytes.extend_from_slice(&raw);
        bytes.extend_from_slice(b"\nendstream\nendobj\n");
        bytes.extend_from_slice(format!("startxref\n{offset}\n%%EOF\n").as_bytes());
        bytes
    }

    fn boundary_xref_stream(filtered: bool) -> Vec<u8> {
        let mut decoded = vec![0_u8; 12 * 8];
        decoded[8] = 1;
        decoded[14] = 9;
        let content = if filtered {
            let mut stream = lopdf::Stream::new(lopdf::Dictionary::new(), decoded);
            stream.compress().expect("boundary xref rows compress");
            stream.content
        } else {
            decoded
        };
        let filter = if filtered {
            "/Filter /FlateDecode "
        } else {
            ""
        };
        let mut bytes = b"%PDF-1.7\n".to_vec();
        let offset = bytes.len();
        bytes.extend_from_slice(
            format!(
                "1 0 obj\n<< /Type /XRef /Size 12 /W [1 6 1] /Index [0 12] {filter}/Length {} >>\nstream\n",
                content.len()
            )
            .as_bytes(),
        );
        bytes.extend_from_slice(&content);
        bytes.extend_from_slice(b"\nendstream\nendobj\n");
        bytes.extend_from_slice(format!("startxref\n{offset}\n%%EOF\n").as_bytes());
        bytes
    }

    fn append_hybrid(base: &[u8], supplemental_id: u32) -> (Vec<u8>, usize) {
        let previous = startxref(base);
        let mut bytes = base.to_vec();
        let stream_offset = bytes.len();
        let row = [0_u8, 0, 0, 0, 0, 0, 1];
        bytes.extend_from_slice(
            format!(
                "6 0 obj\n<< /Type /XRef /Size 7 /W [1 4 2] /Index [{supplemental_id} 1] /Length 7 >>\nstream\n"
            )
            .as_bytes(),
        );
        bytes.extend_from_slice(&row);
        bytes.extend_from_slice(b"\nendstream\nendobj\n");
        let primary_offset = bytes.len();
        bytes.extend_from_slice(
            format!(
                "xref\n6 1\n{stream_offset:010} 00000 n \ntrailer\n<< /Size 7 /Root 1 0 R /Prev {previous} /XRefStm {stream_offset} >>\nstartxref\n{primary_offset}\n%%EOF\n"
            )
            .as_bytes(),
        );
        (bytes, primary_offset)
    }

    fn replace_same_length(bytes: &mut [u8], needle: &[u8], replacement: &[u8]) {
        assert!(replacement.len() <= needle.len());
        let offset = bytes
            .windows(needle.len())
            .position(|window| window == needle)
            .expect("fixture token exists");
        bytes[offset..offset + replacement.len()].copy_from_slice(replacement);
        bytes[offset + replacement.len()..offset + needle.len()].fill(b' ');
    }

    fn swap_normal_xref_offsets(bytes: &mut [u8], first: u32, second: u32) {
        let object_offset = |number: u32| {
            let marker = format!("{number} 0 obj");
            bytes
                .windows(marker.len())
                .position(|window| window == marker.as_bytes())
                .expect("fixture object exists")
        };
        let first_offset = object_offset(first);
        let second_offset = object_offset(second);
        let first_line = format!("{first_offset:010} 00000 n ");
        let second_line = format!("{second_offset:010} 00000 n ");
        let first_line_offset = bytes
            .windows(first_line.len())
            .position(|window| window == first_line.as_bytes())
            .expect("first xref row exists");
        let second_line_offset = bytes
            .windows(second_line.len())
            .position(|window| window == second_line.as_bytes())
            .expect("second xref row exists");
        bytes[first_line_offset..first_line_offset + 10]
            .copy_from_slice(format!("{second_offset:010}").as_bytes());
        bytes[second_line_offset..second_line_offset + 10]
            .copy_from_slice(format!("{first_offset:010}").as_bytes());
    }

    #[test]
    fn raw_xref_preserves_newest_free_tombstone_and_bounded_work() {
        let base = heleos_test_fixtures::pdf_two_pages();
        let updated = append_revision(&base, b"3 1\n0000000000 00001 f \n", "");
        let xref = canonical_xref(&updated, 32, 4096).expect("canonical xref parses");
        assert!(matches!(
            xref.entries.get(&3),
            Some(CanonicalEntry::Free { .. })
        ));
        assert_eq!(
            canonical_xref(&base, 4, 4096),
            Err(PreflightFailure::WorkLimit)
        );
        assert!(canonical_xref(&base, 5, 4096).is_ok());
    }

    #[test]
    fn raw_xref_encrypt_key_precedes_malformed_value_and_later_chain() {
        let base = heleos_test_fixtures::pdf_two_pages();
        let encrypted = append_revision(&base, b"0 1\n0000000000 65535 f \n", "/Encrypt -9");
        assert_eq!(
            canonical_xref(&encrypted, 32, 4096),
            Err(PreflightFailure::Encrypted)
        );

        let encoded = append_revision(&base, b"0 1\n0000000000 65535 f \n", "/Encr#79pt -9");
        assert_eq!(
            canonical_xref(&encoded, 32, 1),
            Err(PreflightFailure::Encrypted),
            "an encoded top-level Encrypt key outranks the metadata cap"
        );

        for inert in [
            "/Synthetic /Encrypt",
            "/Synthetic (/Encrypt)",
            "/Synthetic << /Encrypt -9 >>",
        ] {
            let bytes = append_revision(&base, b"0 1\n0000000000 65535 f \n", inert);
            assert_eq!(
                canonical_xref(&bytes, 32, 1),
                Err(PreflightFailure::Metadata),
                "Encrypt display/value text is not a top-level trailer key: {inert}"
            );
        }
    }

    #[test]
    fn raw_xref_work_limit_precedes_unreached_encryption_trailer() {
        let base = heleos_test_fixtures::pdf_two_pages();
        let encrypted = append_revision(
            &base,
            b"0 3\n0000000000 65535 f \n0000000000 00000 f \n0000000000 00000 f \n",
            "/Encrypt -9",
        );
        assert_eq!(
            canonical_xref(&encrypted, 1, 4096),
            Err(PreflightFailure::WorkLimit),
            "work exhaustion before the authoritative trailer cannot classify unknown encryption"
        );
        assert_eq!(
            canonical_xref(&encrypted, 2, 4096),
            Err(PreflightFailure::Encrypted),
            "encryption wins once the authoritative trailer is reached within budget"
        );
    }

    #[test]
    fn xref_stream_work_limit_precedes_unreached_encryption_dictionary() {
        let encrypted = encrypted_xref_stream();
        assert_eq!(
            canonical_xref(&encrypted, 1, 4096),
            Err(PreflightFailure::WorkLimit)
        );
        assert_eq!(
            canonical_xref(&encrypted, 2, 4096),
            Err(PreflightFailure::Encrypted)
        );
    }

    #[test]
    fn raw_xref_stream_rejects_unknown_type_and_caps_raw_bytes_first() {
        let valid = xref_stream(1, 0);
        assert!(canonical_xref(&valid, 4, 64).is_ok());
        assert_eq!(
            canonical_xref(&xref_stream(3, 0), 4, 64),
            Err(PreflightFailure::Corrupt)
        );
        assert_eq!(
            canonical_xref(&xref_stream(1, 65), 4, 64),
            Err(PreflightFailure::Metadata)
        );

        let mut truncated_after_work_boundary = valid;
        for (from, to) in [
            (b"/Size 2".as_slice(), b"/Size 4".as_slice()),
            (b"/Index [0 2]".as_slice(), b"/Index [0 4]".as_slice()),
        ] {
            let offset = truncated_after_work_boundary
                .windows(from.len())
                .position(|window| window == from)
                .expect("xref-stream fixture field exists");
            truncated_after_work_boundary[offset..offset + to.len()].copy_from_slice(to);
        }
        assert_eq!(
            canonical_xref(&truncated_after_work_boundary, 1, 64),
            Err(PreflightFailure::WorkLimit),
            "unexamined truncated rows cannot outrank xref work exhaustion"
        );

        let mut high_object_number = xref_stream(1, 0);
        high_object_number.splice(9..10, b"4294967296".iter().copied());
        assert_eq!(
            canonical_xref(&high_object_number, 4, 64),
            Err(PreflightFailure::Corrupt)
        );
        let mut high_generation = xref_stream(1, 0);
        let generation = high_generation
            .windows(b" 0 obj".len())
            .position(|window| window == b" 0 obj")
            .expect("xref-stream generation exists")
            + 1;
        high_generation.splice(generation..generation + 1, b"65536".iter().copied());
        assert_eq!(
            canonical_xref(&high_generation, 4, 64),
            Err(PreflightFailure::Corrupt)
        );

        let mut unrepresented_stream_object = xref_stream(1, 0);
        let stream_data = unrepresented_stream_object
            .windows(b"stream\n".len())
            .position(|window| window == b"stream\n")
            .expect("xref stream data exists")
            + b"stream\n".len();
        unrepresented_stream_object[stream_data + 4..stream_data + 8]
            .copy_from_slice(&[0, 0, 0, 0]);
        assert_eq!(
            canonical_xref(&unrepresented_stream_object, 4, 64),
            Err(PreflightFailure::Corrupt),
            "the xref-stream indirect object must be represented in its own revision"
        );
    }

    #[test]
    fn filtered_xref_predictor_row_is_capped_before_lopdf_allocates_it() {
        assert_eq!(
            canonical_xref(&filtered_xref_stream(65), 4, 64),
            Err(PreflightFailure::Metadata)
        );

        let mut malformed_compressed = filtered_xref_stream(65);
        let stream = malformed_compressed
            .windows(b"stream\n".len())
            .position(|window| window == b"stream\n")
            .expect("fixture has a stream")
            + b"stream\n".len();
        malformed_compressed[stream] ^= 0xff;
        assert_eq!(
            canonical_xref(&malformed_compressed, 4, 64),
            Err(PreflightFailure::Metadata),
            "the predictor allocation cap must outrank malformed compressed bytes"
        );
    }

    #[test]
    fn raw_and_decoded_xref_stream_caps_bind_exactly_n_and_n_plus_one() {
        for filtered in [false, true] {
            let bytes = boundary_xref_stream(filtered);
            assert!(
                canonical_xref(&bytes, 12, 96).is_ok(),
                "exact 96-byte {} xref rows must be accepted",
                if filtered { "decoded" } else { "raw" }
            );
            assert_eq!(
                canonical_xref(&bytes, 12, 95),
                Err(PreflightFailure::Metadata),
                "the 96th {} xref byte must exceed a 95-byte cap",
                if filtered { "decoded" } else { "raw" }
            );
        }
    }

    #[test]
    fn raw_xref_handles_current_and_older_hybrid_and_rejects_revision_overlap() {
        let base = heleos_test_fixtures::pdf_two_pages();
        let (hybrid, hybrid_xref) = append_hybrid(&base, 4);
        let canonical = canonical_xref(&hybrid, 64, 4096).expect("current hybrid parses");
        assert!(matches!(
            canonical.entries.get(&4),
            Some(CanonicalEntry::Free { .. })
        ));

        let mut newer = hybrid.clone();
        let newest_xref = newer.len();
        newer.extend_from_slice(
            format!(
                "xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 7 /Root 1 0 R /Prev {hybrid_xref} >>\nstartxref\n{newest_xref}\n%%EOF\n"
            )
            .as_bytes(),
        );
        let canonical = canonical_xref(&newer, 64, 4096).expect("older hybrid parses");
        assert!(matches!(
            canonical.entries.get(&4),
            Some(CanonicalEntry::Free { .. })
        ));

        let (overlap, _) = append_hybrid(&base, 6);
        assert_eq!(
            canonical_xref(&overlap, 64, 4096),
            Err(PreflightFailure::Corrupt)
        );
    }

    #[test]
    fn raw_xref_rejects_cyclic_negative_and_out_of_range_links() {
        let base = heleos_test_fixtures::pdf_two_pages();
        let mut cycle = base.clone();
        let offset = cycle.len();
        cycle.extend_from_slice(
            format!(
                "xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 6 /Root 1 0 R /Prev {offset} >>\nstartxref\n{offset}\n%%EOF\n"
            )
            .as_bytes(),
        );
        assert_eq!(
            canonical_xref(&cycle, 64, 4096),
            Err(PreflightFailure::Corrupt)
        );

        for extra in ["/Prev -1", "/Prev 999999999", "/Prev (bad)"] {
            let bytes = append_revision(&base, b"0 1\n0000000000 65535 f \n", extra);
            assert_eq!(
                canonical_xref(&bytes, 64, 4096),
                Err(PreflightFailure::Corrupt),
                "accepted {extra}"
            );
        }
    }

    #[test]
    fn raw_xref_rejects_out_of_range_normal_and_free_list_offsets() {
        let base = heleos_test_fixtures::pdf_two_pages();
        for body in [
            b"5 1\n0000999999 00000 n \n".as_slice(),
            b"0 1\n0000009999 65535 f \n".as_slice(),
        ] {
            let bytes = append_revision(&base, body, "");
            assert_eq!(
                canonical_xref(&bytes, 64, 4096),
                Err(PreflightFailure::Corrupt)
            );
        }
    }

    #[test]
    fn raw_xref_rejects_swapped_active_normal_offsets_even_when_lopdf_loads_the_same_ids() {
        let mut bytes = heleos_test_fixtures::pdf_two_pages();
        swap_normal_xref_offsets(&mut bytes, 1, 2);
        assert_eq!(
            inspect_pdf_v1(&bytes, &request_for(&bytes)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::Corrupt,
            }
        );
    }

    fn object_stream(header: &[u8], first: i64, count: i64, bodies: &[u8]) -> Object {
        let mut content = header.to_vec();
        content.extend_from_slice(bodies);
        let mut dictionary = lopdf::Dictionary::new();
        dictionary.set("Type", Object::Name(b"ObjStm".to_vec()));
        dictionary.set("N", Object::Integer(count));
        dictionary.set("First", Object::Integer(first));
        Object::Stream(lopdf::Stream::new(dictionary, content))
    }

    #[test]
    fn object_stream_prefilter_records_exact_ordered_ids_and_resets() {
        let header = b"10 0 11 5 ";
        let mut stream = object_stream(header, header.len() as i64, 2, b"null true");
        reset_object_stream_state(1024);
        assert!(object_stream_filter((8, 0), &mut stream).is_some());
        let state = take_object_stream_state().expect("state exists");
        assert_eq!(state.records.get(&(8, 0)), Some(&vec![10, 11]));
        assert!(state.metadata_failure.is_none());
        assert!(state.corrupt_failure.is_none());

        reset_object_stream_state(1024);
        let state = take_object_stream_state().expect("fresh state exists");
        assert!(state.records.is_empty());
    }

    #[test]
    fn object_stream_prefilter_rejects_non_ascii_gaps_and_non_increasing_offsets() {
        for (header, first) in [
            (b"10 0\xc2\xa011 5 ".as_slice(), 13_i64),
            (b"10 0 11 0 ".as_slice(), 10_i64),
            (b"10 0 11 5 x".as_slice(), 12_i64),
        ] {
            let mut stream = object_stream(header, first, 2, b"null true");
            reset_object_stream_state(1024);
            assert!(object_stream_filter((9, 0), &mut stream).is_none());
            let state = take_object_stream_state().expect("state exists");
            assert_eq!(state.corrupt_failure, Some((9, 0)));
        }
    }

    #[test]
    fn object_stream_prefilter_classifies_decompression_limit_without_display_text() {
        let header = b"10 0 ";
        let mut stream = object_stream(header, header.len() as i64, 1, b"null");
        reset_object_stream_state(2);
        assert!(object_stream_filter((7, 0), &mut stream).is_none());
        let state = take_object_stream_state().expect("state exists");
        assert_eq!(state.metadata_failure, Some((7, 0)));
    }

    #[test]
    fn object_stream_invalid_first_is_corrupt_not_a_metadata_limit() {
        let mut stream = object_stream(b"10 0 ", 99, 1, b"null");
        reset_object_stream_state(1024);
        assert!(object_stream_filter((7, 0), &mut stream).is_none());
        let state = take_object_stream_state().expect("state exists");
        assert_eq!(state.corrupt_failure, Some((7, 0)));
        assert!(state.metadata_failure.is_none());
    }

    #[test]
    fn object_stream_prefilter_bounds_declared_counts_and_predictor_rows_before_decode() {
        let Object::Stream(mut excessive_count) = object_stream(b"9 0 ", 4, i64::MAX, b"null")
        else {
            unreachable!();
        };
        assert_eq!(
            validate_object_stream(&excessive_count, 64),
            Err(PreflightFailure::Corrupt)
        );

        excessive_count.dict.set("N", 1);
        excessive_count
            .content
            .extend(std::iter::repeat_n(b' ', 256));
        excessive_count
            .compress()
            .expect("test object stream compresses");
        let mut parameters = lopdf::Dictionary::new();
        parameters.set("Predictor", 15);
        parameters.set("Columns", i64::MAX);
        parameters.set("Colors", i64::MAX);
        excessive_count
            .dict
            .set("DecodeParms", Object::Dictionary(parameters));
        assert_eq!(
            validate_object_stream(&excessive_count, 512),
            Err(PreflightFailure::Corrupt)
        );
    }

    #[test]
    fn non_page_parent_edges_remain_subject_to_reference_depth() {
        let mut document = Document::new();
        document.objects.insert(
            (1, 0),
            Object::Dictionary(lopdf::dictionary! { "Parent" => Object::Reference((2, 0)) }),
        );
        document.objects.insert(
            (2, 0),
            Object::Dictionary(lopdf::dictionary! { "Parent" => Object::Reference((3, 0)) }),
        );
        document
            .objects
            .insert((3, 0), Object::Dictionary(lopdf::Dictionary::new()));
        assert!(validate_reference_depth(&document, 1).is_err());
    }

    #[test]
    fn orphan_page_shaped_parent_edges_are_not_exempt_from_reference_validation() {
        let mut document = Document::load_mem(&heleos_test_fixtures::pdf_two_pages())
            .expect("two-page fixture parses");
        let mut orphan_page = lopdf::Dictionary::new();
        orphan_page.set("Type", Object::Name(b"Page".to_vec()));
        orphan_page.set("Parent", Object::Reference((99, 0)));
        document
            .objects
            .insert((6, 0), Object::Dictionary(orphan_page));

        assert_eq!(
            validate_reference_depth(&document, 64),
            Err(super::ReferenceFailure::Corrupt),
            "only backlinks belonging to the validated page tree are exempt"
        );
    }

    #[test]
    fn page_tree_rejects_stream_objects_even_when_their_dictionaries_look_valid() {
        let mut document = Document::new();
        document.trailer.set("Root", Object::Reference((1, 0)));
        document.objects.insert(
            (1, 0),
            Object::Dictionary(lopdf::dictionary! {
                "Type" => Object::Name(b"Catalog".to_vec()),
                "Pages" => Object::Reference((2, 0))
            }),
        );
        document.objects.insert(
            (2, 0),
            Object::Dictionary(lopdf::dictionary! {
                "Type" => Object::Name(b"Pages".to_vec()),
                "Kids" => Object::Array(vec![Object::Reference((3, 0))]),
                "Count" => Object::Integer(1),
                "MediaBox" => Object::Array(vec![
                    Object::Integer(0),
                    Object::Integer(0),
                    Object::Integer(10),
                    Object::Integer(10),
                ])
            }),
        );
        document.objects.insert(
            (3, 0),
            Object::Stream(lopdf::Stream::new(
                lopdf::dictionary! {
                    "Type" => Object::Name(b"Page".to_vec()),
                    "Parent" => Object::Reference((2, 0))
                },
                Vec::new(),
            )),
        );
        assert!(validated_page_paths(&document, true).is_err());
    }

    fn page_chain_document(page_nodes: u32) -> Document {
        assert!(page_nodes >= 1);
        let mut document = Document::new();
        document.trailer.set("Root", Object::Reference((1, 0)));
        document.objects.insert(
            (1, 0),
            Object::Dictionary(lopdf::dictionary! {
                "Type" => Object::Name(b"Catalog".to_vec()),
                "Pages" => Object::Reference((2, 0))
            }),
        );
        for offset in 0..page_nodes {
            let id = 2 + offset;
            let parent = (offset > 0).then_some(Object::Reference((id - 1, 0)));
            let object = if offset + 1 == page_nodes {
                let mut page = lopdf::dictionary! {
                    "Type" => Object::Name(b"Page".to_vec())
                };
                if let Some(parent) = parent {
                    page.set("Parent", parent);
                }
                Object::Dictionary(page)
            } else {
                let mut pages = lopdf::dictionary! {
                    "Type" => Object::Name(b"Pages".to_vec()),
                    "Kids" => Object::Array(vec![Object::Reference((id + 1, 0))]),
                    "Count" => Object::Integer(1)
                };
                if let Some(parent) = parent {
                    pages.set("Parent", parent);
                }
                Object::Dictionary(pages)
            };
            document.objects.insert((id, 0), object);
        }
        document
    }

    #[test]
    fn page_tree_iteration_is_stack_bounded_and_reference_depth_binds_exactly() {
        let document = page_chain_document(3);
        assert_eq!(
            validated_page_paths(&document, true),
            Ok(vec![vec![(2, 0), (3, 0), (4, 0)]])
        );
        assert_eq!(validate_reference_depth(&document, 4), Ok(()));
        assert_eq!(
            validate_reference_depth(&document, 3),
            Err(super::ReferenceFailure::Limit)
        );

        let deep = page_chain_document(10_000);
        assert_eq!(validated_page_paths(&deep, false), Ok(Vec::new()));
    }

    #[test]
    fn dangling_non_page_reference_is_corrupt_not_a_reference_depth_limit() {
        let mut bytes = heleos_test_fixtures::pdf_two_pages();
        replace_same_length(
            &mut bytes,
            b"/Producer (heleos-synthetic-v1)",
            b"/Synthetic 99 0 R",
        );
        assert_eq!(
            inspect_pdf_v1(&bytes, &request_for(&bytes)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::Corrupt,
            }
        );
    }

    fn request_for(bytes: &[u8]) -> PdfRequestV1 {
        PdfRequestV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                .to_owned(),
            byte_length: bytes.len() as u64,
            limits: PdfDocumentLimitsV1 {
                max_input_bytes: bytes.len() as u64,
                max_pages: 10,
                max_indirect_objects: 64,
                max_nested_references: 16,
                max_metadata_bytes: 16 * 1024,
                max_page_axis_points: 1_000,
            },
        }
    }

    #[test]
    fn inspection_accepts_page_tree_order_geometry_and_ignores_raw_stream_words() {
        let bytes = heleos_test_fixtures::pdf_two_pages();
        let outcome = inspect_pdf_v1(&bytes, &request_for(&bytes));
        let PdfGuestOutcomeV1::Accepted { pages } = outcome else {
            panic!("expected accepted PDF, got {outcome:?}");
        };
        assert_eq!(pages.len(), 2);
        assert_eq!(pages[0].index, 0);
        assert_eq!(
            (pages[0].width_micropoints, pages[0].height_micropoints),
            (612_000_000, 792_000_000)
        );
        assert_eq!(pages[1].rotation_degrees, 90);
        assert_eq!(
            (pages[1].width_micropoints, pages[1].height_micropoints),
            (400_000_000, 300_000_000)
        );

        let harmless = heleos_test_fixtures::pdf_harmless_feature_words();
        let marker = b"/OpenAction /JavaScript /Launch /URI /GoToR /SubmitForm /ImportData /RichMedia /EmbeddedFiles /AF /XFA /AcroForm";
        assert_eq!(
            harmless
                .windows(marker.len())
                .filter(|window| *window == marker)
                .count(),
            3,
            "the fixture must exercise inert page-string, content-stream, and image-stream bytes"
        );
        assert!(matches!(
            inspect_pdf_v1(&harmless, &request_for(&harmless)),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
    }

    #[test]
    fn inspection_uses_frozen_document_precedence_and_active_ordinal() {
        let bad = heleos_test_fixtures::pdf_bad_magic();
        assert_eq!(
            inspect_pdf_v1(&bad, &request_for(&bad)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::BadMagic,
            }
        );
        let corrupt = heleos_test_fixtures::pdf_corrupt_truncated();
        assert_eq!(
            inspect_pdf_v1(&corrupt, &request_for(&corrupt)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::Corrupt,
            }
        );
        for feature in [
            PdfActiveFeatureV1::OpenAction,
            PdfActiveFeatureV1::AdditionalActions,
            PdfActiveFeatureV1::JavaScriptAbbreviation,
            PdfActiveFeatureV1::JavaScript,
            PdfActiveFeatureV1::Launch,
            PdfActiveFeatureV1::Uri,
            PdfActiveFeatureV1::GoToRemote,
            PdfActiveFeatureV1::SubmitForm,
            PdfActiveFeatureV1::ImportData,
            PdfActiveFeatureV1::RichMedia,
            PdfActiveFeatureV1::EmbeddedFiles,
            PdfActiveFeatureV1::AssociatedFiles,
            PdfActiveFeatureV1::Xfa,
            PdfActiveFeatureV1::AcroForm,
        ] {
            let bytes = heleos_test_fixtures::pdf_active_feature(feature);
            assert_eq!(
                inspect_pdf_v1(&bytes, &request_for(&bytes)),
                PdfGuestOutcomeV1::Rejected {
                    reason: PdfGuestReasonV1::ActiveFeature(feature),
                }
            );
        }
        let encoded = heleos_test_fixtures::pdf_encoded_javascript_name();
        assert_eq!(
            inspect_pdf_v1(&encoded, &request_for(&encoded)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::ActiveFeature(PdfActiveFeatureV1::JavaScript),
            }
        );
        let compressed = heleos_test_fixtures::pdf_active_feature_object_stream();
        assert_eq!(
            inspect_pdf_v1(&compressed, &request_for(&compressed)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::ActiveFeature(PdfActiveFeatureV1::JavaScript),
            }
        );

        let mut multiple = heleos_test_fixtures::pdf_active_feature(PdfActiveFeatureV1::JavaScript);
        replace_same_length(
            &mut multiple,
            b"/Producer (heleos-active-v1)",
            b"/OpenAction null",
        );
        assert_eq!(
            inspect_pdf_v1(&multiple, &request_for(&multiple)),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::ActiveFeature(PdfActiveFeatureV1::OpenAction),
            },
            "the lowest frozen active-feature ordinal wins independent of scan order"
        );
    }

    #[test]
    fn inspection_limits_accept_n_and_reject_n_plus_one() {
        let input = heleos_test_fixtures::pdf_input_payload_bytes(37);
        let mut request = request_for(&input);
        request.limits.max_input_bytes = input.len() as u64;
        assert!(matches!(
            inspect_pdf_v1(&input, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
        request.limits.max_input_bytes -= 1;
        assert_eq!(
            inspect_pdf_v1(&input, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::InputBytes),
            }
        );

        let two = heleos_test_fixtures::pdf_page_count(2);
        request = request_for(&two);
        request.limits.max_pages = 2;
        assert!(matches!(
            inspect_pdf_v1(&two, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));

        let three = heleos_test_fixtures::pdf_page_count(3);
        request = request_for(&three);
        request.limits.max_pages = 2;
        assert_eq!(
            inspect_pdf_v1(&three, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::Pages),
            }
        );
    }

    #[test]
    fn inspection_accepts_the_deterministic_ten_thousand_page_default_boundary() {
        let bytes = heleos_test_fixtures::pdf_ten_thousand_pages();
        let mut request = request_for(&bytes);
        request.limits.max_pages = 10_000;
        request.limits.max_indirect_objects = 10_003;
        request.limits.max_metadata_bytes = 16 * 1024 * 1024;

        let PdfGuestOutcomeV1::Accepted { pages } = inspect_pdf_v1(&bytes, &request) else {
            panic!("the default maximum-page fixture must be accepted");
        };
        assert_eq!(pages.len(), 10_000);
        assert_eq!(pages.first().map(|page| page.index), Some(0));
        assert_eq!(pages.last().map(|page| page.index), Some(9_999));
    }

    #[test]
    fn inspection_classifies_both_encryption_paths_before_structure() {
        for bytes in [
            heleos_test_fixtures::pdf_encrypted_password(),
            heleos_test_fixtures::pdf_encrypted_empty_password(),
        ] {
            let mut request = request_for(&bytes);
            request.limits.max_metadata_bytes = 1;
            assert_eq!(
                inspect_pdf_v1(&bytes, &request),
                PdfGuestOutcomeV1::Rejected {
                    reason: PdfGuestReasonV1::Encrypted,
                }
            );
        }
    }

    #[test]
    fn inspection_rejects_page_tree_cycles_repeats_missing_kids_and_counts() {
        for (case, bytes) in [
            ("cycle", heleos_test_fixtures::pdf_page_tree_cycle()),
            (
                "repeated-child",
                heleos_test_fixtures::pdf_page_tree_repeated_child(),
            ),
            (
                "missing-child",
                heleos_test_fixtures::pdf_page_tree_missing_child(),
            ),
            (
                "count-mismatch",
                heleos_test_fixtures::pdf_page_tree_count_mismatch(),
            ),
            (
                "wrong-type",
                heleos_test_fixtures::pdf_page_tree_wrong_type(),
            ),
            (
                "parent-mismatch",
                heleos_test_fixtures::pdf_page_tree_parent_mismatch(),
            ),
            (
                "scalar-child",
                heleos_test_fixtures::pdf_page_tree_scalar_child(),
            ),
        ] {
            assert_eq!(
                inspect_pdf_v1(&bytes, &request_for(&bytes)),
                PdfGuestOutcomeV1::Rejected {
                    reason: PdfGuestReasonV1::Corrupt,
                },
                "page-tree case {case}"
            );
        }
    }

    #[test]
    fn page_tree_root_rejects_a_parent_backlink() {
        let mut document = Document::load_mem(&heleos_test_fixtures::pdf_two_pages())
            .expect("two-page fixture parses");
        let Object::Dictionary(root_pages) = document
            .objects
            .get_mut(&(2, 0))
            .expect("fixture root Pages object exists")
        else {
            panic!("fixture root Pages object is a dictionary");
        };
        root_pages.set("Parent", Object::Reference((1, 0)));

        assert!(
            validated_page_paths(&document, true).is_err(),
            "the root Pages node cannot have a Parent backlink"
        );
    }

    #[test]
    fn inspection_metadata_reference_object_and_axis_limits_bind_n_and_n_plus_one() {
        let metadata = heleos_test_fixtures::pdf_metadata_bytes(512);
        let document = Document::load_mem(&metadata).expect("metadata fixture parses");
        let mut scan = super::DocumentScan::default();
        for object in document.objects.values() {
            super::scan_object(object, &mut scan);
        }
        super::scan_dictionary(&document.trailer, &mut scan);
        let mut request = request_for(&metadata);
        request.limits.max_metadata_bytes = scan.largest_metadata_value_bytes;
        assert!(matches!(
            inspect_pdf_v1(&metadata, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
        request.limits.max_metadata_bytes -= 1;
        assert_eq!(
            inspect_pdf_v1(&metadata, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::MetadataBytes),
            }
        );

        let names = heleos_test_fixtures::pdf_name_bytes(512);
        request = request_for(&names);
        request.limits.max_metadata_bytes = 512;
        assert!(matches!(
            inspect_pdf_v1(&names, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
        request.limits.max_metadata_bytes = 511;
        assert_eq!(
            inspect_pdf_v1(&names, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::MetadataBytes),
            }
        );

        for escaped in [
            heleos_test_fixtures::pdf_escaped_string_bytes(512),
            heleos_test_fixtures::pdf_escaped_name_bytes(512),
        ] {
            request = request_for(&escaped);
            request.limits.max_metadata_bytes = 512;
            assert!(matches!(
                inspect_pdf_v1(&escaped, &request),
                PdfGuestOutcomeV1::Accepted { .. }
            ));
            request.limits.max_metadata_bytes = 511;
            assert_eq!(
                inspect_pdf_v1(&escaped, &request),
                PdfGuestOutcomeV1::Rejected {
                    reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::MetadataBytes),
                }
            );
        }

        let references = heleos_test_fixtures::pdf_reference_depth(3);
        request = request_for(&references);
        request.limits.max_nested_references = 3;
        let reference_outcome = inspect_pdf_v1(&references, &request);
        assert!(
            matches!(reference_outcome, PdfGuestOutcomeV1::Accepted { .. }),
            "reference outcome: {reference_outcome:?}"
        );
        request.limits.max_nested_references = 2;
        assert_eq!(
            inspect_pdf_v1(&references, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::NestedReferences),
            }
        );

        let objects = heleos_test_fixtures::pdf_object_count(6);
        request = request_for(&objects);
        request.limits.max_indirect_objects = 6;
        assert!(matches!(
            inspect_pdf_v1(&objects, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
        request.limits.max_indirect_objects = 5;
        assert_eq!(
            inspect_pdf_v1(&objects, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::IndirectObjects),
            }
        );

        let mut post_bijection_objects = objects.clone();
        let classic_with_free = b"xref\n0 7\n0000000000 65535 f \n";
        let xref = post_bijection_objects
            .windows(classic_with_free.len())
            .position(|window| window == classic_with_free)
            .expect("fixture has the expected classic xref header");
        post_bijection_objects.splice(
            xref..xref + classic_with_free.len(),
            b"xref\n1 6\n".iter().copied(),
        );
        assert!(
            canonical_xref(&post_bijection_objects, 5, 16 * 1024).is_ok(),
            "the active-object N+1 vector must complete raw-xref work before admission"
        );
        request = request_for(&post_bijection_objects);
        request.limits.max_indirect_objects = 5;
        assert_eq!(
            inspect_pdf_v1(&post_bijection_objects, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::IndirectObjects),
            },
            "active-object admission, not xref work exhaustion, rejects N+1"
        );

        let axis = heleos_test_fixtures::pdf_single_page_geometry("0 0 1001 1", None, None, None);
        request = request_for(&axis);
        request.limits.max_page_axis_points = 1_000;
        assert_eq!(
            inspect_pdf_v1(&axis, &request),
            PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::PageAxisPoints),
            }
        );

        let exact_axis =
            heleos_test_fixtures::pdf_single_page_geometry("0 0 1000 1", None, None, None);
        request = request_for(&exact_axis);
        request.limits.max_page_axis_points = 1_000;
        assert!(matches!(
            inspect_pdf_v1(&exact_axis, &request),
            PdfGuestOutcomeV1::Accepted { .. }
        ));
    }

    #[test]
    fn inspection_geometry_user_unit_prompt_and_nested_dictionary_vectors_are_inert() {
        for (case, bytes, reason) in [
            (
                "malformed-media-box",
                heleos_test_fixtures::pdf_malformed_media_box(),
                PdfGuestReasonV1::InvalidGeometry,
            ),
            (
                "missing-media-box",
                heleos_test_fixtures::pdf_missing_media_box(),
                PdfGuestReasonV1::InvalidGeometry,
            ),
            (
                "non-finite-media-box",
                heleos_test_fixtures::pdf_non_finite_media_box(),
                PdfGuestReasonV1::InvalidGeometry,
            ),
            (
                "reversed-media-box",
                heleos_test_fixtures::pdf_single_page_geometry("10 0 1 10", None, None, None),
                PdfGuestReasonV1::InvalidGeometry,
            ),
            (
                "crop-outside-media-box",
                heleos_test_fixtures::pdf_single_page_geometry(
                    "0 0 10 10",
                    Some("-1 0 9 10"),
                    None,
                    None,
                ),
                PdfGuestReasonV1::InvalidGeometry,
            ),
            (
                "unsupported-user-unit",
                heleos_test_fixtures::pdf_single_page_geometry("0 0 10 10", None, None, Some("2")),
                PdfGuestReasonV1::UnsupportedUserUnit,
            ),
            (
                "malformed-user-unit",
                heleos_test_fixtures::pdf_single_page_geometry(
                    "0 0 10 10",
                    None,
                    None,
                    Some("(bad)"),
                ),
                PdfGuestReasonV1::UnsupportedUserUnit,
            ),
        ] {
            assert_eq!(
                inspect_pdf_v1(&bytes, &request_for(&bytes)),
                PdfGuestOutcomeV1::Rejected { reason },
                "geometry case {case}"
            );
        }
        for bytes in [
            heleos_test_fixtures::pdf_prompt_injection(),
            heleos_test_fixtures::pdf_nested_dictionaries(8),
            heleos_test_fixtures::pdf_fractional_nonzero_boxes(),
        ] {
            assert!(matches!(
                inspect_pdf_v1(&bytes, &request_for(&bytes)),
                PdfGuestOutcomeV1::Accepted { .. }
            ));
        }
    }

    #[test]
    fn inspection_classifies_xref_and_object_stream_decompression_bombs() {
        for bytes in [
            heleos_test_fixtures::pdf_xref_stream_decompression_bomb(20_000),
            heleos_test_fixtures::pdf_object_stream_decompression_bomb(20_000),
        ] {
            let mut request = request_for(&bytes);
            request.limits.max_metadata_bytes = 16 * 1024;
            assert_eq!(
                inspect_pdf_v1(&bytes, &request),
                PdfGuestOutcomeV1::Rejected {
                    reason: PdfGuestReasonV1::LimitExceeded(PdfDocumentLimitV1::MetadataBytes),
                }
            );
        }
    }
}
