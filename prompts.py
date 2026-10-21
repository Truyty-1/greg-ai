import random
import skills as skills_module

def is_comparison_question(question):
    keywords = [
        'difference', 'compare', 'comparison', 'vs', 'versus',
        'better', 'contrast', 'distinguish', 'differentiate',
        'pros and cons', 'advantages', 'disadvantages', 'benefits',
        'between', 'differ'
    ]
    return any(keyword in question.lower() for keyword in keywords)

def generate_enhanced_prompt(question):
    hexa1 = hex(random.randint(4096, 65535))[2:]
    hexa2 = hex(random.randint(65536, 131071))[2:]

    start_tag = f"[début_{hexa1}]"
    end_tag = f"[fin_{hexa2}]"

    skills_instructions = skills_module.registry.get_prompt_description()

    enhanced_question = f"""

Voici les SKILLS que tu peux utiliser si nécessaire :
{skills_instructions}

CONSIGNES :
- Si tu as besoin d'utiliser un ou plusieurs outils, renvoie le bloc JSON d'appel du skill (`use_skill`).
- Si tu as la réponse finale ou que tu n'as plus besoin de skill, réponds directement en texte clair à l'utilisateur.

SI TU UTILLISE UN SKILL NE MARQUE PAS DE PHRASE AUTRE QUE LE SKILL


début tag nombre = {hexa1}
fin tag nombre = {hexa2}

REQUÊTE / RÉSULTAT REÇU :
{question}
"""


    return enhanced_question, start_tag, end_tag, hexa1, hexa2