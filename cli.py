"""Quick terminal test loop. Run: python cli.py"""
from agent import JarvisAgent
import scheduler

def main():
    scheduler.start()
    agent = JarvisAgent()
    print("Jarvis is listening (text mode). Type 'quit' to exit.\n")
    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in {"quit", "exit"}:
            break
        reply = agent.run(user_text)
        print(f"Jarvis: {reply}\n")

if __name__ == "__main__":
    main()
