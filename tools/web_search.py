
import argparse
import json
import sys

def search_web(query, num_results=5):
    """
    Simulates a web search. In a real implementation, this would call Google Search API/SerpApi.
    """
    # Mock results
    results = [
        {"title": f"Result {i+1} for {query}", "url": f"https://example.com/result{i+1}", "snippet": f"This is a snippet for result {i+1} about {query}..."}
        for i in range(num_results)
    ]
    return results

def main():
    parser = argparse.ArgumentParser(description="Search the web for information.")
    parser.add_argument("--query", type=str, required=True, help="The search query.")
    parser.add_argument("--num_results", type=int, default=5, help="Number of results to return.")
    
    args = parser.parse_args()
    
    try:
        results = search_web(args.query, args.num_results)
        print(json.dumps(results, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
