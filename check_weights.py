import sys
import re

def check_weights():
    try:
        with open("scripts/config.toml", "r") as f:
            content = f.read()
        
        sections = [
            "postclose.trend", "postclose.activity", "postclose.stability", "postclose.score",
            "tail.trend", "tail.activity", "tail.stability", "tail.score"
        ]
        
        results = []
        for section in sections:
            # Simple regex to find the section and its key-value pairs
            # This handles [section.sub] or [section]
            pattern = rf'\[{section}\](.*?)(?=\n\[|$)'
            match = re.search(pattern, content, re.DOTALL)
            
            if not match:
                results.append(f"Section {section} missing.")
                continue
            
            block = match.group(1)
            # Find all numeric values like key = 0.5
            weights = re.findall(r'=\s*([\d\.]+)', block)
            total = sum(float(w) for w in weights)
            
            if not (0.99 <= total <= 1.01):
                results.append(f"{section}: sum={total}")
        
        if results:
            print("\n".join(results))
            sys.exit(1)
        else:
            print("All sections sum to approximately 1.0")
            sys.exit(0)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    check_weights()
