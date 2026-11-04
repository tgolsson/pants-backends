fn main() {
    println!("Hello, world6!");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hello() {
        assert_eq!(2 + 2, 4);
    }
}
