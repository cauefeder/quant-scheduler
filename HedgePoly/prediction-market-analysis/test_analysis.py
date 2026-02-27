"""Test script to run analyses on Windows."""
from pathlib import Path
from src.common.analysis import Analysis

def main():
    # Load all available analyses
    analyses = Analysis.load()
    print(f"\nFound {len(analyses)} analyses available:\n")
    
    for i, analysis_cls in enumerate(analyses, 1):
        instance = analysis_cls()
        print(f"{i:2d}. {instance.name}")
        print(f"    {instance.description}\n")
    
    # Run a simple analysis as a test
    print("\n" + "="*60)
    print("Running test analysis: meta_stats")
    print("="*60 + "\n")
    
    for analysis_cls in analyses:
        instance = analysis_cls()
        if instance.name == "meta_stats":
            output_dir = Path("output")
            output_dir.mkdir(exist_ok=True)
            
            print("Generating outputs...")
            saved = instance.save(output_dir, formats=["png", "csv", "json"])
            
            print("\n✓ Analysis complete! Saved files:")
            for fmt, path in saved.items():
                print(f"  {fmt}: {path}")
            
            return
    
    print("Analysis 'meta_stats' not found!")

if __name__ == "__main__":
    main()
