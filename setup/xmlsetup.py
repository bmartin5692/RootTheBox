# -*- coding: utf-8 -*-
"""
Created on Aug 26, 2013

    Copyright 2012 Root the Box

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
------------------------------------------------------------------------------

This file wraps the Python scripted game setup API.
It reads an XML file(s) and calls the API based on the it's contents.

"""
# pylint: disable=unused-wildcard-import


import binascii
import logging
from base64 import b64decode
from os import listdir, path, urandom
from shutil import copyfile

import defusedxml.cElementTree as ET
from tornado.options import options

from libs.ConfigHelpers import save_config, save_config_image
from libs.StringCoding import decode, encode, set_type
from models import dbsession
from models.Box import FlagsSubmissionType

# We have to import all of the classes to avoid mapper errors
from setup.create_database import *


def get_child_by_tag(elem, tag_name):
    """Return child elements with a given tag"""
    tags = [child for child in elem if child.tag == tag_name]
    return tags[0] if 0 < len(tags) else None


def get_child_text(elem, tag_name, default=""):
    """Shorthand access to .text data"""
    try:
        text = get_child_by_tag(elem, tag_name).text
        if text == "None" or text is None:
            return default
        else:
            return text
    except:
        return default


def create_categories(categories):
    """Create Category objects based on XML data"""
    if categories is None:
        return
    logging.info("Found %s categories" % categories.get("count"))
    for index, cat_elem in enumerate(categories):
        cat = get_child_text(cat_elem, "category")
        desc = get_child_text(cat_elem, "description")
        if Category.by_category(cat) is None:
            try:
                category = Category()
                category.category = cat
                category.description = desc
                dbsession.add(category)
            except:
                logging.exception("Failed to import category #%d" % (index + 1))
    dbsession.commit()


def create_levels(levels):
    """Create GameLevel objects based on XML data"""
    if levels is None:
        return
    logging.info("Found %s game level(s)" % levels.get("count"))
    missing_level_buyouts = {}
    for index, level_elem in enumerate(levels):
        # GameLevel 0 is created automatically by the bootstrap
        try:
            number = get_child_text(level_elem, "number")            
            if GameLevel.by_number(number) is None:
                game_level = GameLevel()
                game_level.number = int(number)
                game_level.name = get_child_text(level_elem, "name")
                game_level.description = get_child_text(level_elem, "description")
                game_level.type = get_child_text(level_elem, "type")
                game_level.reward = get_child_text(level_elem, "reward", 0)
                game_level.buyout = get_child_text(level_elem, "buyout", 0)
                if game_level.type == "level":
                    buyoutlevel = GameLevel.by_number(game_level.buyout)
                    if buyoutlevel:
                        game_level.buyout = buyoutlevel.id
                    else:
                        missing_level_buyouts[game_level.number] = game_level.buyout
                dbsession.add(game_level)
            else:
                logging.info("GameLevel %d already exists, skipping" % int(number))
        except:
            logging.exception("Failed to import game level #%d" % (index + 1))
    dbsession.commit()
    dbsession.flush()
    game_levels = GameLevel.all()
    if len(missing_level_buyouts):
        logging.info(f"Missing buyouts to update: {len(missing_level_buyouts)}")
    for index, game_level in enumerate(game_levels):
        if index + 1 < len(game_levels):
            game_level.next_level_id = game_levels[index + 1].id
            logging.info("%r -> %r" % (game_level, game_levels[index + 1]))
            missing_buyout_level = missing_level_buyouts.get(game_level.number, None)            
            if missing_buyout_level:
                logging.info(f"attempting update for level {game_level.id} buyout after level {missing_buyout_level}")
                buyoutlevel = GameLevel.by_number(missing_buyout_level)
                if buyoutlevel:                    
                    game_level.buyout = buyoutlevel.id
                    logging.info(f"game_level {game_level.id} buyout {missing_buyout_level} -> {buyoutlevel.id}")
            
            # dbsession.add(game_level)
    dbsession.commit()


def create_hints(parent, box, flag=None):
    """Create flag objects for a box"""
    if parent and box:
        logging.info("Found %s hint(s)" % parent.get("count"))
        for index, hint_elem in enumerate(parent):
            try:
                flag_id = None
                if flag:
                    flag_id = flag.id
                hint = Hint(box_id=box.id, flag_id=flag_id)
                hint.price = get_child_text(hint_elem, "price", 0)
                hint.description = get_child_text(hint_elem, "description")
                dbsession.add(hint)
            except:
                logging.exception("Failed to import hint #%d" % (index + 1))


def create_flags(parent, box):
    """Create flag objects for a box"""
    if parent and box:
        logging.info("Found %s flag(s)" % parent.get("count"))
        flag_dependency = []
        for index, flag_elem in enumerate(parent):
            try:
                flag = Flag(box_id=box.id)
                flag.name = get_child_text(flag_elem, "name")
                flag.token = get_child_text(flag_elem, "token")
                flag.value = get_child_text(flag_elem, "value", 10)
                flag._plain_answer = get_child_text(flag_elem, "plain_answer")
                flag.case_sensitive = get_child_text(flag_elem, "case_sensitive", 0)
                flag.description = get_child_text(flag_elem, "description")
                flag.capture_message = get_child_text(flag_elem, "capture_message")
                flag.type = flag_elem.get("type", "static")
                flag.order = get_child_text(flag_elem, "order", None)
                flag.locked = get_child_text(flag_elem, "locked", 0)
                if flag.type == "file":
                    add_attachments(
                        get_child_by_tag(flag_elem, "flag_attachments"), flag
                    )
                dbsession.add(flag)
                dbsession.flush()
                depend = get_child_text(flag_elem, "depends_on", None)
                if depend:
                    flag_dependency.append({"flag": flag, "name": depend})
                if flag.type == "choice":
                    create_choices(get_child_by_tag(flag_elem, "flag_choices"), flag)
                create_hints(get_child_by_tag(flag_elem, "hints"), box, flag)
            except:
                logging.exception("Failed to import flag #%d" % (index + 1))
        if len(flag_dependency) > 0:
            for item in flag_dependency:
                for flag in sorted(box.flags):
                    if item["name"] == flag.name:
                        item["flag"].lock_id = flag.id
                        continue


def add_attachments(parent, flag):
    """Add uploaded files as attachments to flags"""
    if flag is None:
        return
    logging.info("Found %s attachment(s)" % parent.get("count"))
    for index, attachment_elem in enumerate(parent):
        try:
            flag_attachment = FlagAttachment(
                file_name=get_child_text(attachment_elem, "flag_name")
            )
            flag_attachment.data = bytearray(
                b64decode(get_child_text(attachment_elem, "data"))
            )
            flag.flag_attachments.append(flag_attachment)
            dbsession.add(flag_attachment)
        except:
            logging.exception("Failed to import attachment #%d in flag" % (index + 1))


def create_choices(parent, flag):
    """Create multiple choice flag objects"""
    if flag is None:
        return
    logging.info("Found %s choice(s)" % parent.get("count"))
    for index, choice_elem in enumerate(parent):
        try:
            choice = FlagChoice(flag_id=flag.id)
            choice.choice = choice_elem.text
            dbsession.add(choice)
        except:
            logging.exception("Failed to import choice #%d in flag" % (index + 1))


def create_boxes(parent, corporation):
    """Create boxes for a corporation"""
    if corporation is None:
        return
    logging.info("Found %s boxes" % parent.get("count"))
    for index, box_elem in enumerate(parent):
        try:
            name = get_child_text(box_elem, "name")
            game_level = GameLevel.by_number(get_child_text(box_elem, "gamelevel", "0"))
            if game_level is None:
                logging.warning("GameLevel does not exist for box %s, skipping" % name)
            elif Box.by_name(name) is None:
                box = Box(corporation_id=corporation.id)
                box.name = name
                box.game_level_id = game_level.id
                box.difficulty = get_child_text(box_elem, "difficulty")
                box.flag_submission_type = FlagsSubmissionType[
                    get_child_text(box_elem, "flag_submission_type", "CLASSIC")
                ]

                box.description = get_child_text(box_elem, "description")
                box.capture_message = get_child_text(box_elem, "capture_message")
                box.operating_system = get_child_text(box_elem, "operatingsystem")
                box.locked = get_child_text(box_elem, "locked", 0)
                box.value = get_child_text(box_elem, "value", "0")
                box_order = get_child_text(box_elem, "order", None)
                if box_order:
                    box._order = int(box_order)
                if get_child_text(box_elem, "avatar", "none") != "none":
                    avatar_path = get_child_text(box_elem, "avatar_path", "upload")
                    box.avatar = (bytearray(
                        b64decode(get_child_text(box_elem, "avatar"))
                    ), avatar_path)
                box.garbage = get_child_text(
                    box_elem, "garbage", binascii.hexlify(urandom(16)).decode()
                )
                category = get_child_text(box_elem, "category")
                if category:
                    box.category_id = Category.by_category(category).id
                dbsession.add(box)
                dbsession.flush()
                create_flags(get_child_by_tag(box_elem, "flags"), box)
                create_hints(get_child_by_tag(box_elem, "hints"), box)
            else:
                logging.info("Box with name %s already exists, skipping" % name)
        except BaseException as e:
            logging.exception("Failed to import box %d (%s)" % (index + 1, e))


def create_corps(corps):
    """Create Corporation objects based on XML data"""
    if corps is None:
        return
    logging.info("Found %s corporation(s)" % corps.get("count"))
    for index, corp_elem in enumerate(corps):
        try:
            corporation = Corporation()
            corporation.name = get_child_text(corp_elem, "name", "")
            if Corporation.by_name(corporation.name) is None:
                dbsession.add(corporation)
                dbsession.flush()
            else:
                corporation = Corporation.by_name(corporation.name)
            create_boxes(get_child_by_tag(corp_elem, "boxes"), corporation)
        except BaseException as e:
            logging.exception("Failed to create corporation #%d (%s)" % (index + 1, e))


def update_configuration(config):
    """Update Configuration options based on XML data"""
    if config is None:
        return
    """ Backup configuration """
    copyfile(options.config, options.config + ".bak")
    images = ["ctf_logo", "story_character", "scoreboard_right_image"]
    for config_elem in config:
        try:
            if options[config_elem.tag] is not None:
                if config_elem.tag in images:
                    value = save_config_image(get_child_text(config, config_elem.tag))
                elif isinstance(options[config_elem.tag], list):
                    lines = []
                    for line in get_child_by_tag(config, config_elem.tag):
                        lines.append(line.text)
                    value = lines
                else:
                    value = get_child_text(config, config_elem.tag)
                value = set_type(value, options[config_elem.tag])
                if isinstance(value, type(options[config_elem.tag])):
                    logging.info("Configuration (%s): %s" % (config_elem.tag, value))
                    options[config_elem.tag] = value
                else:
                    logging.error(
                        "Confirguation (%s): unable to convert type %s to %s for %s"
                        % (
                            config_elem.tag,
                            type(value),
                            type(options[config_elem.tag]),
                            value,
                        )
                    )
        except BaseException as e:
            logging.exception("Failed to update configuration (%s)" % e)
    save_config()
    
def check_import_options(options):
    """Check and handle import options based on XML data"""
    
    # User can add the following to the XML <import_options clear_levels="1" clear_corps="1" />
    if options is None:
        return
    
    try:
        clear_levels = int(options.get("clear_levels", "0"))
    except:
        clear_levels = 0
        pass
    
    if clear_levels == 1:
        logging.info("Clearing Levels Before Import")
        game_levels = GameLevel.all()
        for level in game_levels:
            dbsession.delete(level)
        dbsession.commit()
    
    try:
        clear_corps = int(options.get("clear_corps", "0"))
    except:
        clear_corps = 0
    
    if clear_corps == 1:
        logging.info("Clearing Corporations Before Import")
        corps = Corporation.all()
        for corp in corps:
            dbsession.delete(corp)
        dbsession.commit()

def _xml_file_import(filename):
    """Parse and import a single XML file"""
    logging.debug("Processing: %s" % filename)
    try:
        tree = ET.parse(filename)
        xml_root = tree.getroot()
        import_options = get_child_by_tag(xml_root, "import_options")
        check_import_options(import_options)
        levels = get_child_by_tag(xml_root, "gamelevels")
        create_levels(levels)
        categories = get_child_by_tag(xml_root, "categories")
        create_categories(categories)
        corporations = get_child_by_tag(xml_root, "corporations")
        create_corps(corporations)
        configuration = get_child_by_tag(xml_root, "configuration")
        update_configuration(configuration)
        logging.debug("Done processing: %s" % filename)
        dbsession.commit()
        return True
    except:
        dbsession.rollback()
        logging.exception(
            "Exception raised while parsing %s, rolling back changes" % filename
        )
        return False


def import_xml(target):
    """Import XML file or directory of files"""
    target = path.abspath(path.expanduser(target))
    if not path.exists(target):
        logging.error("Error: Target does not exist (%s) " % target)
    elif path.isdir(target):
        # Import any .xml files in the target directory
        logging.debug("%s is a directory ..." % target)
        ls = [fname for fname in listdir(target) if fname.lower().endswith(".xml")]
        logging.debug("Found %d XML file(s) ..." % len(ls))
        results = [_xml_file_import(target + "/" + fxml) for fxml in ls]
        return False not in results
    else:
        # Import a single file
        return _xml_file_import(target)
