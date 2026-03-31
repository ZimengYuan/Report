---
layout: default
title: 晚间研究
permalink: /research/evening/
---

<h1>晚间研究</h1>

<ul>
{% assign evening_reports = site.research | where: "public_report", true | where: "type", "evening" | sort: "title" %}
{% for doc in evening_reports %}
    <li><a href="{{ doc.url | relative_url }}">{{ doc.title }}</a>{% if doc.date %} - {{ doc.date | date: "%Y-%m-%d" }}{% endif %}</li>
{% endfor %}
</ul>
