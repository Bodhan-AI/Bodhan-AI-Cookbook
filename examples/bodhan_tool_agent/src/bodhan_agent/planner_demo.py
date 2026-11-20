from bodhan_agent.planner import create_plan

plan = create_plan(
    task="Translate this Bengali text into English.",
    files=["sample_data/test_document.png"],
)

print("\nGenerated Plan:")
print(plan)