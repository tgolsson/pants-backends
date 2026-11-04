#[tokio::main]
async fn main() -> Result<(), reqwest::Error> {
    let url = if let Some(url) = std::env::args().nth(1) {
        url
    } else {
        println!("No CLI URL provided, using default.");
        "https://hyper.rs".into()
    };

    eprintln!("Fetching {:?}...", url);
    let res = reqwest::get(url).await?;

    eprintln!("Response: {:?} {}", res.version(), res.status());
    eprintln!("Headers: {:#?}\n", res.headers());

    let body = res.text().await?;

    println!("{}", body);

    Ok(())
}
