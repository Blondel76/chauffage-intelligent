Chauffage Intelligent

Intégration personnalisée pour Home Assistant
 permettant de gérer intelligemment le chauffage pièce par pièce.

L'intégration utilise la température intérieure, la température extérieure, la consigne du thermostat et un coefficient de chauffe pour estimer le temps nécessaire à la montée en température.

Elle permet également de définir des plannings de chauffage selon différents modes, d'anticiper le démarrage du chauffage et d'ajuster progressivement le coefficient de chauffe en fonction de la vitesse réelle de montée en température.

Fonctionnalités
Configuration centrale de l'installation.
Gestion du type de chauffage :
Gaz
Électrique
Gestion du chauffage pièce par pièce.
Association d'un thermostat Home Assistant à chaque pièce.
Utilisation d'un capteur de température intérieure.
Utilisation d'un capteur de température extérieure.
Association d'une entité de chauffe.
Gestion optionnelle d'un capteur de porte/fenêtre.
Interrupteur virtuel pour simuler l'ouverture d'une fenêtre lorsqu'aucun capteur n'est configuré.
Gestion de groupes de pièces.
Plannings différents selon le mode sélectionné dans Home Assistant.
Calcul du temps de chauffe estimé.
Calcul de l'heure de démarrage anticipé.
Calcul de l'heure du prochain changement de planning.
Calcul de l'heure du planning précédent.
Apprentissage progressif du coefficient de chauffe.
Conservation du coefficient après redémarrage de Home Assistant.
Installation
Avec HACS

L'intégration est conçue pour être installée comme intégration personnalisée via HACS.

Dans HACS :

Ouvrir HACS.
Aller dans Intégrations.
Rechercher Chauffage Intelligent.
Installer l'intégration.
Redémarrer Home Assistant.

Si le dépôt n'est pas encore disponible dans la liste officielle HACS, il peut être ajouté comme dépôt personnalisé.

Installation manuelle

Copier le dossier :

custom_components/chauffage_intelligent


dans :

/config/custom_components/


La structure doit être :

/config/
└── custom_components/
    └── chauffage_intelligent/
        ├── __init__.py
        ├── calculations.py
        ├── config_flow.py
        ├── const.py
        ├── manifest.json
        ├── number.py
        ├── resolver.py
        ├── scheduler.py
        ├── sensor.py
        ├── switch.py
        └── translations/


Redémarrer ensuite Home Assistant.

Configuration

Après l'installation :

Paramètres → Appareils et services → Ajouter une intégration → Chauffage Intelligent

La configuration commence par la configuration centrale.

Configuration centrale

La configuration centrale demande :

l'entité input_select utilisée pour sélectionner le mode de chauffage ;
le type de chauffage :
Gaz
Électrique

La configuration centrale est unique pour l'installation.

Configuration d'une pièce

Pour chaque pièce, l'intégration utilise :

une zone Home Assistant ;
un capteur de température intérieure ;
un capteur de température extérieure ;
un thermostat climate ;
une entité de chauffe ;
éventuellement un capteur de porte/fenêtre.

Le type d'entité de chauffe dépend du type de chauffage configuré :

chauffage électrique : switch
chauffage gaz : climate
Plannings

Chaque mode disponible dans l'input_select central peut avoir son propre planning.

Le planning utilise le format :

HH:MM|mode


Plusieurs changements peuvent être séparés par des virgules.

Exemple :

06:30|comfort,08:30|eco,17:00|comfort,22:30|eco


Le contenu exact du deuxième élément dépend de la logique de chauffage utilisée par la configuration.

Fonctionnement

L'intégration calcule le temps de chauffe estimé à partir de plusieurs paramètres :

température intérieure ;
température extérieure ;
température de consigne ;
coefficient de chauffe.

Le coefficient est limité à une plage comprise entre 10 et 60 et sa valeur initiale est 25.

Le coefficient peut ensuite évoluer automatiquement en fonction de la dérive observée de la température intérieure.

Anticipation

Lorsque le prochain changement de planning est connu, l'intégration calcule une heure de démarrage anticipée en fonction du temps de chauffe estimé.

Exemple :

Planning :       07:00
Temps estimé :   35 minutes

Démarrage :      06:25


Si le temps nécessaire est trop important ou si aucune anticipation fiable ne peut être calculée, l'heure du planning est conservée.

Entités créées

Pour chaque pièce, l'intégration crée notamment :

Capteurs
Temps de chauffe
Dérive
Heure planning
Heure planning précédent
Heure anticipée
Nombre
Coefficient

Le coefficient peut être ajusté manuellement et est également susceptible d'être ajusté automatiquement.

Interrupteur

Lorsqu'aucun capteur de porte/fenêtre n'est configuré, l'intégration crée :

Fenêtre ouverte (manuel)

Cet interrupteur permet de simuler manuellement l'ouverture d'une fenêtre.

Groupes

L'intégration permet de créer des groupes de pièces.

Un groupe contient :

un nom ;
plusieurs pièces ;
un seuil de température.

Les groupes permettent de regrouper des pièces thermiquement liées.

Apprentissage du chauffage

Le coefficient de chauffe est progressivement ajusté à partir de la dérive observée de la température intérieure lorsque le thermostat indique que le chauffage est actif.

L'objectif est d'obtenir progressivement une estimation plus adaptée au comportement thermique réel de chaque pièce.

Le coefficient est sauvegardé par Home Assistant et restauré après redémarrage.

Structure du projet
chauffage-intelligent/
├── custom_components/
│   └── chauffage_intelligent/
│       ├── __init__.py
│       ├── calculations.py
│       ├── config_flow.py
│       ├── const.py
│       ├── manifest.json
│       ├── number.py
│       ├── resolver.py
│       ├── scheduler.py
│       ├── sensor.py
│       ├── switch.py
│       └── translations/
│           ├── en.json
│           └── fr.json
│
├── tests/
│   ├── conftest.py
│   ├── test_calculations.py
│   ├── test_config_flow.py
│   ├── test_resolver.py
│   └── test_scheduler.py
│
├── .github/
│   └── workflows/
│       └── tests.yml
│
├── .gitignore
├── hacs.json
├── LICENSE
└── README.md

Développement

Les tests sont exécutés avec pytest.

Après installation des dépendances de développement :

pytest


Pour vérifier la compilation Python :

python -m compileall custom_components

État du projet

Le projet est actuellement en développement.

Les comportements liés au scheduler, à l'apprentissage du coefficient et aux groupes sont susceptibles d'évoluer.

Les contributions, rapports de bugs et suggestions sont les bienvenus.

Licence

Ce projet est distribué sous licence MIT.

Voir le fichier LICENSE.

Auteur

Développé par @Blondel76.
