/// Generate a fresh session auth token for the Python sidecar.
///
/// The token is a 128-bit random hex string, regenerated every time the
/// desktop app launches. The sidecar checks the `X-Session-Token` header
/// on every request and rejects calls without a valid token.
use rand::Rng;

pub fn generate_session_token() -> String {
    let token: String = rand::thread_rng()
        .sample_iter(&rand::distributions::Alphanumeric)
        .take(32)
        .map(char::from)
        .collect();
    token
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_32_chars() {
        let t = generate_session_token();
        assert_eq!(t.len(), 32);
    }

    #[test]
    fn tokens_are_unique() {
        let t1 = generate_session_token();
        let t2 = generate_session_token();
        assert_ne!(t1, t2);
    }
}
