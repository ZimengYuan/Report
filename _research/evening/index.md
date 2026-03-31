---
layout: default
title: 晚间研究
permalink: /research/evening/
---

<h1>晚间研究</h1>

<ul>
{% assign evening_reports = site.research | where: "type", "evening" | sort: "title" %}
{% for doc in evening_reports %}
    <li><a href="{{ doc.url }}">{{ doc.title }}</a></li>
{% endfor %}
</ul>
